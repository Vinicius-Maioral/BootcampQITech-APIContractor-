[CmdletBinding()]
param([switch]$GuideOnly)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    $python = Join-Path $projectRoot '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $python)) { throw 'Create .venv and install requirements-dev.txt first' }
    if ($GuideOnly) {
        & $python -m pytest -v -m contract_guide --junitxml=artifacts/contract-guide.xml
    } else {
        & $python -m pytest -v
    }
    $testExit = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $testExit
