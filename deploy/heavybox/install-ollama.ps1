# deploy/heavybox/install-ollama.ps1 — Phase 11a-A2
# Run as Administrator on the heavy box (Windows + WSL — Ollama runs
# native). Installs as a Windows service so it survives WoL wake
# without anyone logging in. Pulls the REASONING + CODING + heavy
# LOCAL_ONLY defaults.

#Requires -RunAsAdministrator

$ErrorActionPreference = "Stop"

Write-Host "==> Installing Ollama (Windows)..."
$installer = "$env:TEMP\OllamaSetup.exe"
Invoke-WebRequest -Uri "https://ollama.com/download/OllamaSetup.exe" -OutFile $installer
Start-Process -FilePath $installer -ArgumentList "/SILENT" -Wait

Write-Host "==> Configuring Ollama service to listen on 0.0.0.0:11434..."
[System.Environment]::SetEnvironmentVariable("OLLAMA_HOST", "0.0.0.0:11434", "Machine")
[System.Environment]::SetEnvironmentVariable("OLLAMA_KEEP_ALIVE", "5m", "Machine")

Write-Host "==> Restarting Ollama service to pick up env vars..."
Stop-Service -Name "Ollama" -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Start-Service -Name "Ollama"

Write-Host "==> Configuring Ollama service-recovery policy (watchdog)..."
# Phase 11a-A2: auto-restart on crash. 3 restarts in 24h with 60s delay
# between each. The idle-suspend task is the OTHER side of this — it
# deliberately stops Ollama (well, suspends the host); recovery only
# kicks in on actual crashes.
& sc.exe failure "Ollama" reset= 86400 actions= restart/60000/restart/60000/restart/60000

Write-Host "==> Pulling default REASONING / heavy LOCAL_ONLY / CODING models..."
Write-Host "    (this is slow — total ~80GB across 3 models)"
& ollama pull qwen2.5:32b
& ollama pull llama3.3:70b
& ollama pull qwen2.5-coder:32b

Write-Host "==> Smoke-testing the API from the heavy box itself..."
$response = Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -TimeoutSec 10
if ($response.models.Count -lt 3) {
    Write-Error "Ollama returned <3 models after install"
    exit 1
}
Write-Host "OK Heavy-box Ollama install complete. Models loaded:"
$response.models | ForEach-Object { Write-Host "  - $($_.name)" }
