@echo off
rem One-click setup for the free Cloudflare quick tunnel to your AI gateway.
rem Installs cloudflared (if missing), checks the gateway, then runs scripts\install-tunnel-task.ps1
rem (starts the tunnel now and at every logon). Safe to run again.
cd /d "%~dp0.."
echo.
echo === 1/3  cloudflared ===
where cloudflared >nul 2>nul
if errorlevel 1 (
  if exist "%ProgramFiles(x86)%\cloudflared\cloudflared.exe" (echo already installed) else if exist "%ProgramFiles%\cloudflared\cloudflared.exe" (echo already installed) else (
    winget install --id Cloudflare.cloudflared -e --accept-source-agreements --accept-package-agreements
  )
) else (echo already installed)
echo.
echo === 2/3  AI gateway reachable? ===
powershell -NoProfile -Command "$u=[Uri]((Get-Content .env | Where-Object { $_ -match '^AI_PRIMARY_URL=' } | Select-Object -First 1) -replace '^AI_PRIMARY_URL=',''); if(-not $u){$u=[Uri]'http://192.168.0.226:8000/v1'}; if((Test-NetConnection $u.Host -Port $u.Port -WarningAction SilentlyContinue).TcpTestSucceeded){Write-Host ('OK - ' + $u.Authority) -ForegroundColor Green}else{Write-Host ('NOT reachable: ' + $u.Authority + '  (is the gateway PC on?) - the tunnel will still start and work once it is.') -ForegroundColor Yellow}"
echo.
echo === 3/3  tunnel task ===
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-tunnel-task.ps1"
echo.
echo Waiting 25 s for the tunnel to come up...
timeout /t 25 /nobreak >nul
powershell -NoProfile -Command "Get-Content \"$env:LOCALAPPDATA\portfolio-tunnel\tunnel.log\" -Tail 5"
echo.
pause
