# Starts the failsafe AI (Gemma 3 12B on Ollama in Docker) and downloads the model once (~8 GB).
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\start-fallback-ai.ps1
Set-Location (Split-Path $PSScriptRoot -Parent)

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Docker is not running - start Docker Desktop first." -ForegroundColor Red
    exit 1
}

Write-Host "Starting Ollama with GPU..." -ForegroundColor Cyan
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d ollama
if ($LASTEXITCODE -ne 0) {
    Write-Host "GPU start failed - starting on CPU instead (slower, still works as a failsafe)." -ForegroundColor Yellow
    docker compose -f docker-compose.yml up -d ollama
    if ($LASTEXITCODE -ne 0) { exit 1 }
}

Start-Sleep -Seconds 3
Write-Host "Pulling gemma3:12b (first time only, about 8 GB)..." -ForegroundColor Cyan
docker exec portfolio-gemma ollama pull gemma3:12b
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host "Failsafe AI ready: http://localhost:11434/v1  (model gemma3:12b)" -ForegroundColor Green
