@echo off
rem Starts the Cloudflare tunnel for your website, hidden. For every logon, put a SHORTCUT to this file
rem in your Startup folder (Win+R, type shell:startup, Enter). Running it twice is safe: only one copy runs.
start "" powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "%~dp0start-tunnel.ps1"
