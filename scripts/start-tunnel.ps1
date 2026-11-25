# Exposes your AI gateway to Vercel with a FREE Cloudflare quick tunnel (no account, no domain)
# and keeps the site pointed at it, even though the trycloudflare.com URL changes on every restart.
#
#   1. starts:  cloudflared tunnel --url <the gateway's internet door, http://127.0.0.1:8001>
#               (that door only accepts the gateway's secret internet_key - see the AI Server README)
#   2. reads the random https://xxxx.trycloudflare.com address it prints
#   3. seals the address + internet_key with SECRET_KEY (scripts\publish_endpoint.py) and writes the
#      sealed record to the data Gist - only the website's backend can open it. The address is never
#      written to this script's log.
#   4. every AUTO_SYNC_MINUTES asks the live site to sync GitHub + AI-categorise NEW repos
#   5. if cloudflared stops, starts it again (new URL -> published again)
#
# Needs in .env: GIST_ID, GITHUB_TOKEN (or GIST_TOKEN), SECRET_KEY (same as on Vercel),
#                AI_GATEWAY_KEYS_FILE (path to the gateway's gateway-keys.json);
#                SITE_URL + CRON_SECRET for step 4. Optional: AI_TUNNEL_ORIGIN, PYTHON.
# Needs Python 3 (the gateway already uses it) and cloudflared:  winget install --id Cloudflare.cloudflared
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\start-tunnel.ps1
# Run at every logon: a shortcut to scripts\start-tunnel.cmd in your Startup folder (Win+R, shell:startup),
#                     or the scheduled task from scripts\install-tunnel-task.ps1. Only one copy ever runs:
#                     a second start (Startup folder + task, or a double-click) just exits.
param(
    [string]$Origin = ""   # override the gateway's internet door (default http://127.0.0.1:8001)
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
    try { Add-Content -Path $logFile -Value $line } catch {}
}

# One copy at a time - before anything else, so a second copy can't stop the first one's tunnel.
$mutex = New-Object System.Threading.Mutex($false, "Local\PortfolioAITunnel")
$owned = $false
try { $owned = $mutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $owned = $true }
if (-not $owned) {
    Log "The tunnel script is already running - this copy exits."
    exit 0
}

# Older versions of this script wrote the tunnel's address into the log - take those out.
if (Test-Path $logFile) {
    try {
        $old = Get-Content -Path $logFile -Raw -ErrorAction Stop
        $clean = [regex]::Replace($old, 'https://[a-z0-9-]+\.trycloudflare\.com\S*', '[address hidden]')
        if ($clean -ne $old) { Set-Content -Path $logFile -Value $clean -NoNewline }
    } catch {}
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
$siteUrl = (Get-Setting $envVals "SITE_URL").TrimEnd("/")
$cronSecret = Get-Setting $envVals "CRON_SECRET"
$syncMinutes = [Math]::Max(5, [int](Get-Setting $envVals "AUTO_SYNC_MINUTES" "60"))

if ([string]::IsNullOrWhiteSpace($gistId) -or [string]::IsNullOrWhiteSpace($token)) {
    Log "ERROR: GIST_ID and GITHUB_TOKEN (or GIST_TOKEN) must be set in .env"
    exit 1
}

if ([string]::IsNullOrWhiteSpace($Origin)) { $Origin = Get-Setting $envVals "AI_TUNNEL_ORIGIN" "http://127.0.0.1:8001" }
$originUri = [Uri]$Origin
$apiPath = "/v1"
$publisher = Join-Path $PSScriptRoot "publish_endpoint.py"
# The gateway's keys (publish_endpoint.py reads the same file); a change is published again.
$keysFile = Get-Setting $envVals "AI_GATEWAY_KEYS_FILE" (Join-Path (Split-Path $root -Parent) "AI Server\gateway-keys.json")
function Get-KeysStamp {
    try { return (Get-Item -LiteralPath $keysFile -ErrorAction Stop).LastWriteTimeUtc.Ticks } catch { return 0 }
}

# Python for publish_endpoint.py (standard library only): PYTHON in .env, else python, else the py launcher.
$python = @()
$pySetting = Get-Setting $envVals "PYTHON"
# The first real python.exe on PATH (not the Microsoft Store shortcut), as the gateway's .bat uses.
$pyExe = Get-Command python -All -CommandType Application -ErrorAction SilentlyContinue |
    Where-Object { $_.Source -notlike "*\WindowsApps\*" } | Select-Object -First 1
if ($pySetting) { $python = @($pySetting) }
elseif ($pyExe) { $python = @($pyExe.Source) }
elseif (Get-Command py -ErrorAction SilentlyContinue) { $python = @("py", "-3") }
if ($python.Count -eq 0) {
    Log "ERROR: Python 3 not found (needed to seal the tunnel record). Install it or set PYTHON=<path> in .env"
    exit 1
}

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

function Publish-Sealed([string]$url) {
    # Seals {address, internet_key} with SECRET_KEY and writes it to the Gist. Prints nothing secret.
    $exe = $python[0]
    $pre = @($python | Select-Object -Skip 1)
    $ErrorActionPreference = "Continue"   # Python's own messages on stderr must not stop this script
    $out = & $exe @pre $publisher --url $url 2>&1 | Out-String
    $ok = ($LASTEXITCODE -eq 0)
    Log ("publish: " + $out.Trim())
    return $ok
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

# A previous run (maybe pointing at another address) may have left its cloudflared behind - stop it.
Get-CimInstance Win32_Process -Filter "Name='cloudflared.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*portfolio-tunnel*" -or $_.CommandLine -like "*--url $Origin*" -or
                   $_.CommandLine -like "*--url http://localhost:8000*" -or $_.CommandLine -like "*--url http://127.0.0.1:8000*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

function Wait-Origin([int]$seconds) {
    # True once something answers on the gateway's port (it may start after the tunnel at boot).
    $u = $originUri
    $deadline = (Get-Date).AddSeconds($seconds)
    do {
        $c = New-Object Net.Sockets.TcpClient
        try { if ($c.ConnectAsync($u.Host, $u.Port).Wait(1000) -and $c.Connected) { return $true } } catch {} finally { $c.Close() }
        if ($seconds -gt 1) { Start-Sleep -Seconds 5 }
    } while ((Get-Date) -lt $deadline)
    return $false
}

Log "Gateway internet door: $Origin  ->  Cloudflare quick tunnel (its address is published sealed; never logged here)"
$backoff = 5
while ($true) {
  try {
    $cfLog = Join-Path $logDir "cloudflared.log"
    $cfErr = Join-Path $logDir "cloudflared.err.log"
    foreach ($f in @($cfLog, $cfErr)) { if (Test-Path $f) { Remove-Item $f -Force -ErrorAction SilentlyContinue } }
    $proc = Start-Process -FilePath $cloudflared -PassThru -WindowStyle Hidden -RedirectStandardError $cfErr `
        -ArgumentList @("tunnel", "--no-autoupdate", "--url", $Origin, "--http-host-header", $originUri.Authority, "--logfile", "`"$cfLog`"")

    $url = $null
    for ($i = 0; $i -lt 90 -and -not $proc.HasExited -and -not $url; $i++) {
        Start-Sleep -Seconds 1
        $text = (Read-Shared $cfErr) + "`n" + (Read-Shared $cfLog)
        $m = [regex]::Match($text, 'https://[a-z0-9-]+\.trycloudflare\.com')
        if ($m.Success) { $url = $m.Value }
    }

    if ($url) {
        $api = $url + $apiPath
        $url = $null
        # Publish once the gateway answers (it creates gateway-keys.json on its first start).
        $up = Wait-Origin 900
        if (-not $up) { Log "Gateway $Origin not up after 15 min - publishing anyway" }
        $keysStamp = Get-KeysStamp
        $published = Publish-Sealed $api
        if ($published) {
            Log "Tunnel up (address kept private - sealed record published)"
            $backoff = 5
        } else {
            Log "Tunnel up, but the sealed record could not be published yet - trying again every 2 minutes"
        }
        Start-Sleep -Seconds 30          # let Vercel re-read the Gist (it checks every 20 s)
        if ($published -and ($up -or (Wait-Origin 1))) { Invoke-SiteSync }
        $nextSync = (Get-Date).AddMinutes($syncMinutes)
        $nextTry = (Get-Date).AddMinutes(2)
        while (-not $proc.HasExited) {
            Start-Sleep -Seconds 15
            if ($published -and (Get-KeysStamp) -ne $keysStamp) {
                Log "The gateway's keys changed - publishing the sealed record again"
                $published = $false
                $nextTry = Get-Date
            }
            if (-not $published -and (Get-Date) -ge $nextTry) {
                $keysStamp = Get-KeysStamp
                $published = Publish-Sealed $api
                if ($published) { Log "Sealed record published (address kept private)"; $backoff = 5 }
                $nextTry = (Get-Date).AddMinutes(2)
            }
            if ((Get-Date) -ge $nextSync) {
                if (Wait-Origin 1) { Invoke-SiteSync } else { Log "Gateway $Origin is down - skipping this sync" }
                $nextSync = (Get-Date).AddMinutes($syncMinutes)
            }
        }
        $api = $null
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
