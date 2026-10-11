[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'docker-common.ps1')
Push-Location $projectRoot
$savedEnvironment = @{}
$restoreApi = @{}
$apiChanged = $false
$suiteLock = $null
$testExit = 1
$names = @('PARTY_API_URL', 'LEDGER_API_URL', 'PARTY_API_TIMEOUT', 'LEDGER_API_TIMEOUT', 'CONTRACT_DOCKER_TESTS', 'CONTRACT_TEST_DATABASE_URL')
try {
    foreach ($name in $names) { $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
    New-Item -ItemType Directory -Force -Path '.pytest_cache' | Out-Null
    # Two suites must not reset the same WireMock scenarios concurrently.
    $suiteLock = [IO.File]::Open((Join-Path $projectRoot '.pytest_cache/wiremock.lock'), [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    Wait-Healthy 'db'
    Wait-Healthy 'api'
    $containerId = & docker compose ps -q api
    $current = (& docker inspect --format '{{json .Config.Env}}' $containerId | ConvertFrom-Json)
    foreach ($entry in $current) {
        $pair = $entry -split '=', 2
        if ($pair[0] -in $names[0..3]) { $restoreApi[$pair[0]] = $pair[1] }
    }
    foreach ($name in $names[0..3]) {
        if (-not $restoreApi.ContainsKey($name)) { throw "Running API is missing $name" }
    }
    $config = ((& docker compose config --format json) -join "`n") | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read Compose configuration' }
    $env:PARTY_API_URL = 'http://party-mock:8080'
    $env:LEDGER_API_URL = 'http://ledger-mock:8080'
    $env:PARTY_API_TIMEOUT = '1'
    $env:LEDGER_API_TIMEOUT = '1'
    $env:CONTRACT_DOCKER_TESTS = '1'
    $databasePort = $config.services.db.ports[0].published
    $env:CONTRACT_TEST_DATABASE_URL = $config.services.api.environment.DATABASE_URL.Replace('@db:5432/', "@localhost:$databasePort/")
    Invoke-Docker -Arguments @('compose', 'up', '-d', '--no-deps', 'party-mock', 'ledger-mock')
    Wait-Healthy 'party-mock'
    Wait-Healthy 'ledger-mock'
    $apiChanged = $true
    Invoke-Docker -Arguments @('compose', 'up', '-d', '--no-deps', 'api')
    Wait-Healthy 'api'
    $python = Join-Path $projectRoot '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $python)) { throw 'Create .venv and install requirements-dev.txt first' }
    & $python -m pytest -q
    $testExit = $LASTEXITCODE
} finally {
    try {
        if ($apiChanged) {
            foreach ($name in $names[0..3]) { [Environment]::SetEnvironmentVariable($name, $restoreApi[$name], 'Process') }
            Invoke-Docker -Arguments @('compose', 'up', '-d', '--no-deps', 'api')
            Wait-Healthy 'api'
        }
    } finally {
        foreach ($name in $names) {
            if ($savedEnvironment.ContainsKey($name)) { [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process') }
        }
        if ($suiteLock) { $suiteLock.Dispose() }
        Pop-Location
    }
}
exit $testExit
