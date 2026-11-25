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
echo === 2/3  AI gateway's internet door (this PC, port 8001) answering? ===
powershell -NoProfile -Command "$o=(Get-Content .env -ErrorAction SilentlyContinue | Where-Object { $_ -match '^AI_TUNNEL_ORIGIN=' } | Select-Object -First 1) -replace '^AI_TUNNEL_ORIGIN=',''; if(-not $o){$o='http://127.0.0.1:8001'}; $u=[Uri]$o; if((Test-NetConnection $u.Host -Port $u.Port -WarningAction SilentlyContinue).TcpTestSucceeded){Write-Host 'OK - the gateway is running on this PC' -ForegroundColor Green}else{Write-Host 'Not answering yet (start the gateway on THIS PC: AI Server\3_start_gateway.bat) - the tunnel will still start and work once it is.' -ForegroundColor Yellow}"
echo.
echo === 3/3  tunnel task ===
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-tunnel-task.ps1"
echo.
echo Waiting 25 s for the tunnel to come up...
timeout /t 25 /nobreak >nul
powershell -NoProfile -Command "Get-Content \"$env:LOCALAPPDATA\portfolio-tunnel\tunnel.log\" -Tail 5"
echo.
pause
