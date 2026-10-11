$ErrorActionPreference = 'Stop'

function Invoke-Docker {
    param([string[]]$Arguments)
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Docker failed: $($Arguments[0]) $($Arguments[1]) (exit $LASTEXITCODE)" }
}

function Wait-Healthy {
    param([string]$Service)
    $deadline = (Get-Date).AddSeconds(120)
    do {
        $containerId = & docker compose ps -a -q $Service
        if ($LASTEXITCODE -ne 0) { throw "Cannot inspect service $Service" }
        if ($containerId) {
            $state = & docker inspect --format '{{json .State}}' $containerId | ConvertFrom-Json
            if ($state.Health.Status -eq 'healthy') { Write-Host "$Service healthy"; return }
            if ($state.Status -eq 'exited' -or $state.Health.Status -eq 'unhealthy') {
                & docker compose logs --tail 50 $Service
                throw "$Service failed to start"
            }
        }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    throw "Timed out waiting for $Service"
}
