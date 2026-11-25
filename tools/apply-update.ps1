# Drive the DuoRemote panel's own update flow over its local API (on the PC).
# Usage: powershell -File apply-update.ps1 [targetVersion]
# No target = whatever the panel's update check reports as latest.
param([string]$Target = '')
$ErrorActionPreference = 'Stop'
$repo = Join-Path $env:USERPROFILE 'duo-remote'
$lockPath = Join-Path $repo 'server\panel.lock'

function Read-Lock { Get-Content $lockPath -Raw | ConvertFrom-Json }
function Call-Api($lock, $method, $path) {
    $headers = @{ 'X-Panel-Key' = $lock.key }
    Invoke-RestMethod -Method $method -Uri "http://127.0.0.1:$($lock.port)$path" -Headers $headers -TimeoutSec 30
}

$lock = Read-Lock
$status = Call-Api $lock 'GET' '/api/status'
Write-Host "current version: $($status.version)"
$check = Call-Api $lock 'GET' '/api/update'
Write-Host "update check: current=$($check.current) latest=$($check.latest) available=$($check.available) error=$($check.error)"
if (-not $Target) { $Target = $check.latest }
if ($status.version -eq $Target) {
    Write-Host "already at target version $Target"
    exit 0
}
if (-not $check.available) {
    Write-Host "panel says no update available toward $Target; stop"
    exit 1
}

$res = Call-Api $lock 'POST' '/api/update'
Write-Host "update started: $($res.started)"

$deadline = (Get-Date).AddMinutes(4)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 5
    try {
        $lock = Read-Lock
        $status = Call-Api $lock 'GET' '/api/status'
        Write-Host "poll: version=$($status.version)"
        if ($status.version -eq $Target) {
            Write-Host "DONE: panel now reports $Target"
            exit 0
        }
    } catch {
        Write-Host 'poll: panel restarting...'
    }
}
Write-Host "TIMEOUT waiting for $Target"
exit 1
