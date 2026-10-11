[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot

. (Join-Path $PSScriptRoot 'docker-common.ps1')

try {
    Invoke-Docker -Arguments @('info', '--format', '{{.ServerVersion}}')
    Invoke-Docker -Arguments @('compose', 'config', '--quiet')
    Invoke-Docker -Arguments @('compose', 'up', '-d', '--no-recreate', 'db')
    Wait-Healthy 'db'

    $backupDirectory = Join-Path $projectRoot 'backups'
    New-Item -ItemType Directory -Force -Path $backupDirectory | Out-Null
    $backupName = 'bootcamp-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N') + '.dump'
    $backupPath = Join-Path $backupDirectory $backupName
    Invoke-Docker -Arguments @('compose', 'exec', '-T', 'db', 'pg_dump', '-U', 'bootcamp', '-d', 'bootcamp', '-Fc', '-f', "/tmp/$backupName")
    Invoke-Docker -Arguments @('compose', 'cp', "db:/tmp/$backupName", $backupPath)
    if ((Get-Item -LiteralPath $backupPath).Length -eq 0) { throw 'Database backup is empty' }
    Write-Host "Backup saved: $backupPath"

    # Compose retains anonymous volumes on recreation; verify the data volume.
    $databaseContainer = & docker compose ps -q db
    $mountsBefore = (& docker inspect --format '{{json .Mounts}}' $databaseContainer | ConvertFrom-Json)
    $dataMountBefore = ($mountsBefore | Where-Object Destination -eq '/var/lib/postgresql/data').Name
    Invoke-Docker -Arguments @('compose', 'up', '-d', '--no-deps', 'db')
    Wait-Healthy 'db'
    $databaseContainer = & docker compose ps -q db
    $mountsAfter = (& docker inspect --format '{{json .Mounts}}' $databaseContainer | ConvertFrom-Json)
    $dataMountAfter = ($mountsAfter | Where-Object Destination -eq '/var/lib/postgresql/data').Name
    if (-not $dataMountBefore -or $dataMountBefore -ne $dataMountAfter) { throw 'Database data volume changed; restore the original volume before continuing' }


    Invoke-Docker -Arguments @('compose', 'build', 'api')
    Invoke-Docker -Arguments @('compose', 'pull', 'party-mock', 'ledger-mock')
    $schemaSource = Join-Path $projectRoot 'database'
    # Apply and verify within the same database transaction; rollback on any mismatch.
    Invoke-Docker -Arguments @('compose', 'run', '--rm', '--no-deps', '--volume', "${schemaSource}:/schema:ro", 'api', 'python', '/schema/prepare.py')
    Invoke-Docker -Arguments @('compose', 'up', '-d', '--no-deps', 'party-mock', 'ledger-mock')
    Wait-Healthy 'party-mock'
    Wait-Healthy 'ledger-mock'
    # Retire only the previous Cadastro simulator from this Compose project.
    # No volume removal: the old Ledger state remains available in Docker.
    $composeConfig = ((& docker compose config --format json) -join "`n") | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read Compose project name' }
    $legacyContainers = & docker ps -a --filter "label=com.docker.compose.project=$($composeConfig.name)" --filter 'label=com.docker.compose.service=registry-mock' --format '{{.ID}}'
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect legacy simulator' }
    foreach ($legacyContainer in $legacyContainers) {
        Invoke-Docker -Arguments @('rm', '--force', $legacyContainer)
    }
    Invoke-Docker -Arguments @('compose', 'up', '-d', '--no-deps', 'api')
    Wait-Healthy 'api'
    Invoke-Docker -Arguments @('compose', 'ps')
    Write-Host 'Local environment ready. Run scripts/demo-contract.ps1 to exercise the API.'
} finally {
    Pop-Location
}
