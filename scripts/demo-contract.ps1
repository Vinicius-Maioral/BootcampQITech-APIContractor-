[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    $rawConfig = & docker compose config --format json
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read Docker configuration' }
    $config = ($rawConfig -join "`n") | ConvertFrom-Json
    $port = $config.services.api.ports[0].published
    $baseUrl = "http://localhost:$port"
    $headers = @{'INTERNAL-TOKEN' = $config.services.api.environment.INTERNAL_TOKEN; 'X-Request-ID' = ('demo-' + [guid]::NewGuid().ToString('N'))}
    $payload = Get-Content (Join-Path $projectRoot 'examples/contract.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $payload.external_reference = 'DEMO-' + [guid]::NewGuid().ToString('N')
    $json = $payload | ConvertTo-Json -Depth 20
    $created = Invoke-RestMethod -Uri "$baseUrl/contracts" -Method Post -Headers $headers -ContentType 'application/json; charset=utf-8' -Body $json
    $contractKey = $created.contract_key
    $audit = @{actor='local-demo'} | ConvertTo-Json
    $draft = Invoke-RestMethod -Uri "$baseUrl/contracts/$contractKey" -Headers $headers
    if ($draft.status -ne 'draft') { throw 'Expected draft' }
    Invoke-RestMethod -Uri "$baseUrl/contracts/$contractKey/activation" -Method Post -Headers $headers -ContentType 'application/json' -Body $audit | Out-Null
    $active = Invoke-RestMethod -Uri "$baseUrl/contracts/$contractKey" -Headers $headers
    if ($active.status -ne 'active' -or -not $active.provisioned_at) { throw 'Activation not provisioned' }
    $payload.rent_amount_in_cents = 530000
    $newSplit = @{actor='local-demo'; rent_amount_in_cents=$payload.rent_amount_in_cents; split=$payload.split} | ConvertTo-Json -Depth 20
    $version = Invoke-RestMethod -Uri "$baseUrl/contracts/$contractKey/split_versions" -Method Post -Headers $headers -ContentType 'application/json' -Body $newSplit
    if ($version.split_version -ne 2) { throw 'Expected split version 2' }
    $listed = Invoke-RestMethod -Uri "$baseUrl/contracts?client_key=$($payload.client_key)&external_reference=$($payload.external_reference)" -Headers $headers
    if ($listed.contracts.Count -ne 1) { throw 'Created contract not listed' }
    Invoke-RestMethod -Uri "$baseUrl/contracts/$contractKey/termination" -Method Post -Headers $headers -ContentType 'application/json' -Body $audit | Out-Null
    $closed = Invoke-RestMethod -Uri "$baseUrl/contracts/$contractKey" -Headers $headers
    if ($closed.status -ne 'terminated' -or $closed.split_version -ne 2 -or $closed.status_events.Count -ne 3) { throw 'Invalid final state' }
    Write-Host "PASS: contract $contractKey, draft -> active -> terminated; two versions and three events."
} finally {
    Pop-Location
}
