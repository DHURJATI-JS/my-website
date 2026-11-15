# Exposes your AI gateway to Vercel with a FREE Cloudflare quick tunnel (no account, no domain)
# and keeps the site pointed at it, even though the trycloudflare.com URL changes on every restart.
#
#   1. starts:  cloudflared tunnel --url <your gateway, from AI_PRIMARY_URL in .env>
#   2. reads the random https://xxxx.trycloudflare.com address it prints
#   3. writes it to the data Gist (file ai-endpoint.json) - Vercel picks it up within ~20 s
#   4. every AUTO_SYNC_MINUTES asks the live site to sync GitHub + AI-categorise NEW repos
#   5. if cloudflared stops, starts it again (new URL -> published again)
#
# Needs in .env: GIST_ID, GITHUB_TOKEN (or GIST_TOKEN), AI_PRIMARY_URL; SITE_URL + CRON_SECRET for step 4.
# Install cloudflared once:  winget install --id Cloudflare.cloudflared
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\start-tunnel.ps1
# Run at every logon:  powershell -ExecutionPolicy Bypass -File scripts\install-tunnel-task.ps1
param(
    [string]$Origin = ""   # override the gateway address, e.g. http://192.168.0.226:8000
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$root = Split-Path $PSScriptRoot -Parent
$logDir = Join-Path $env:LOCALAPPDATA "portfolio-tunnel"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir "tunnel.log"

function Log([string]$msg) {
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Write-Host $line
    Add-Content -Path $logFile -Value $line
}

function Read-DotEnv([string]$path) {
    $vals = @{}
    if (Test-Path $path) {
        foreach ($line in Get-Content -Path $path -Encoding UTF8) {
            if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
                $vals[$Matches[1]] = $Matches[2].Trim().Trim('"').Trim("'")
            }
        }
    }
    return $vals
}

function Get-Setting($envVals, [string]$name, [string]$default = "") {
    $v = [Environment]::GetEnvironmentVariable($name)
    if ([string]::IsNullOrWhiteSpace($v)) { $v = $envVals[$name] }
    if ([string]::IsNullOrWhiteSpace($v)) { $v = $default }
    return $v
}

$envVals = Read-DotEnv (Join-Path $root ".env")
$gistId = Get-Setting $envVals "GIST_ID"
$token = Get-Setting $envVals "GIST_TOKEN"
if ([string]::IsNullOrWhiteSpace($token)) { $token = Get-Setting $envVals "GITHUB_TOKEN" }
$endpointFile = Get-Setting $envVals "AI_ENDPOINT_FILE" "ai-endpoint.json"
$primary = Get-Setting $envVals "AI_PRIMARY_URL" "http://192.168.0.226:8000/v1"
$siteUrl = (Get-Setting $envVals "SITE_URL").TrimEnd("/")
$cronSecret = Get-Setting $envVals "CRON_SECRET"
$syncMinutes = [Math]::Max(5, [int](Get-Setting $envVals "AUTO_SYNC_MINUTES" "60"))

if ([string]::IsNullOrWhiteSpace($gistId) -or [string]::IsNullOrWhiteSpace($token)) {
    Log "ERROR: GIST_ID and GITHUB_TOKEN (or GIST_TOKEN) must be set in .env"
    exit 1
}

$primaryUri = [Uri]$primary
if ([string]::IsNullOrWhiteSpace($Origin)) {
    $Origin = "{0}://{1}:{2}" -f $primaryUri.Scheme, $primaryUri.Host, $primaryUri.Port
}
$apiPath = $primaryUri.AbsolutePath.TrimEnd("/")   # usually /v1

$cloudflared = (Get-Command cloudflared -ErrorAction SilentlyContinue).Source
if (-not $cloudflared) {
    foreach ($p in @("$env:ProgramFiles\cloudflared\cloudflared.exe", "${env:ProgramFiles(x86)}\cloudflared\cloudflared.exe",
                     "$env:LOCALAPPDATA\Microsoft\WinGet\Links\cloudflared.exe")) {
        if (Test-Path $p) { $cloudflared = $p; break }
    }
}
if (-not $cloudflared) {
    Log "ERROR: cloudflared not found. Install it:  winget install --id Cloudflare.cloudflared"
    exit 1
}

$gistHeaders = @{
    Authorization          = "Bearer $token"
    Accept                 = "application/vnd.github+json"
    "X-GitHub-Api-Version" = "2022-11-28"
    "User-Agent"           = "portfolio-tunnel"
}

function Publish-Url([string]$url) {
    $payload = @{ url = $url; origin = $Origin; updated_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ"); host = $env:COMPUTERNAME } |
        ConvertTo-Json -Compress
    $body = @{ files = @{ $endpointFile = @{ content = $payload } } } | ConvertTo-Json -Depth 5
    Invoke-RestMethod -Method Patch -Uri "https://api.github.com/gists/$gistId" -Headers $gistHeaders `
        -Body ([Text.Encoding]::UTF8.GetBytes($body)) -ContentType "application/json; charset=utf-8" | Out-Null
}

function Invoke-SiteSync {
    if ([string]::IsNullOrWhiteSpace($siteUrl) -or [string]::IsNullOrWhiteSpace($cronSecret)) { return }
    try {
        $r = Invoke-RestMethod -Uri "$siteUrl/api/cron/sync" -Headers @{ Authorization = "Bearer $cronSecret" } -TimeoutSec 90
        $done = @($r.categorized).Count
        $left = @($r.deferred).Count
        Log ("site sync: categorised {0}, still waiting {1}, errors {2}" -f $done, $left, @($r.errors).Count)
    } catch {
        Log "site sync failed: $($_.Exception.Message)"
    }
}

function Read-Shared([string]$path) {
    # cloudflared keeps its log open; read it without locking.
    try {
        $fs = [IO.File]::Open($path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
        try { return (New-Object IO.StreamReader($fs)).ReadToEnd() } finally { $fs.Dispose() }
    } catch { return "" }
}

# A previous run that died may have left its cloudflared behind - stop it.
Get-CimInstance Win32_Process -Filter "Name='cloudflared.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*--url $Origin*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Log "Gateway: $Origin  ->  Cloudflare quick tunnel (gist $($gistId.Substring(0, [Math]::Min(7, $gistId.Length)))..., file $endpointFile)"
$backoff = 5
while ($true) {
  try {
    $cfLog = Join-Path $logDir "cloudflared.log"
    $cfErr = Join-Path $logDir "cloudflared.err.log"
    foreach ($f in @($cfLog, $cfErr)) { if (Test-Path $f) { Remove-Item $f -Force -ErrorAction SilentlyContinue } }
    $proc = Start-Process -FilePath $cloudflared -PassThru -WindowStyle Hidden -RedirectStandardError $cfErr `
        -ArgumentList @("tunnel", "--no-autoupdate", "--url", $Origin, "--http-host-header", $primaryUri.Authority, "--logfile", "`"$cfLog`"")

    $url = $null
    for ($i = 0; $i -lt 90 -and -not $proc.HasExited -and -not $url; $i++) {
        Start-Sleep -Seconds 1
        $text = (Read-Shared $cfErr) + "`n" + (Read-Shared $cfLog)
        $m = [regex]::Match($text, 'https://[a-z0-9-]+\.trycloudflare\.com')
        if ($m.Success) { $url = $m.Value }
    }

    if ($url) {
        try {
            Publish-Url ($url + $apiPath)
            Log "Tunnel up: $url$apiPath  (published to the Gist)"
            $backoff = 5
        } catch {
            Log "Could not write the Gist: $($_.Exception.Message)"
        }
        Start-Sleep -Seconds 30          # let Vercel re-read the Gist (it checks every 20 s)
        Invoke-SiteSync
        $nextSync = (Get-Date).AddMinutes($syncMinutes)
        while (-not $proc.HasExited) {
            Start-Sleep -Seconds 15
            if ((Get-Date) -ge $nextSync) { Invoke-SiteSync; $nextSync = (Get-Date).AddMinutes($syncMinutes) }
        }
        Log "cloudflared exited (code $($proc.ExitCode)) - restarting"
    } else {
        Log "No trycloudflare URL appeared - see $cfErr"
        if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force }
        $backoff = [Math]::Min(300, $backoff * 2)
    }
  } catch {
    Log "Error: $($_.Exception.Message)"
    $backoff = [Math]::Min(300, $backoff * 2)
  }
  Start-Sleep -Seconds $backoff
}
