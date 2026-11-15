# Runs scripts\start-tunnel.ps1 hidden at every logon (and starts it now).
#   Install:  powershell -ExecutionPolicy Bypass -File scripts\install-tunnel-task.ps1
#   Remove:   powershell -ExecutionPolicy Bypass -File scripts\install-tunnel-task.ps1 -Remove
param([switch]$Remove)

$ErrorActionPreference = "Stop"
$name = "Portfolio AI tunnel"
$script = Join-Path $PSScriptRoot "start-tunnel.ps1"

if ($Remove) {
    Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force
    Write-Host "Removed '$name'." -ForegroundColor Yellow
    exit 0
}

$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`"" `
    -WorkingDirectory (Split-Path $PSScriptRoot -Parent)
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName $name
Write-Host "'$name' installed and started. Log: $env:LOCALAPPDATA\portfolio-tunnel\tunnel.log" -ForegroundColor Green
