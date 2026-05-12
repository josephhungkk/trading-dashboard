# deploy/nuc/install-ollama.ps1 — Phase 11a-A2
# Run as Administrator on the NUC15PRO. Installs Ollama as a Windows
# service so it survives reboot without login. Pulls the LOCAL_ONLY +
# STRUCTURED_OUTPUT default models. Verifies the API responds.

#Requires -RunAsAdministrator

$ErrorActionPreference = "Stop"

Write-Host "==> Installing Ollama (Windows)..."
$installer = "$env:TEMP\OllamaSetup.exe"
Invoke-WebRequest -Uri "https://ollama.com/download/OllamaSetup.exe" -OutFile $installer
Start-Process -FilePath $installer -ArgumentList "/SILENT" -Wait

Write-Host "==> Configuring Ollama service to listen on 0.0.0.0:11434..."
# Recent Ollama installers register as a per-user service; we set the
# OLLAMA_HOST env var system-wide so it binds to 0.0.0.0 and is
# reachable from the WG-routed BE on the VPS.
[System.Environment]::SetEnvironmentVariable("OLLAMA_HOST", "0.0.0.0:11434", "Machine")
[System.Environment]::SetEnvironmentVariable("OLLAMA_KEEP_ALIVE", "5m", "Machine")

Write-Host "==> Restarting Ollama service to pick up env vars..."
Stop-Service -Name "Ollama" -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Start-Service -Name "Ollama"

Write-Host "==> Pulling default LOCAL_ONLY models (this takes a while)..."
& ollama pull qwen2.5:7b
& ollama pull llama3.2:8b

Write-Host "==> Smoke-testing the API..."
$response = Invoke-RestMethod -Uri "http://10.10.0.2:11434/api/tags" -TimeoutSec 10
if ($response.models.Count -lt 2) {
    Write-Error "Ollama returned <2 models after install"
    exit 1
}
Write-Host "OK NUC Ollama install complete. Models loaded:"
$response.models | ForEach-Object { Write-Host "  - $($_.name)" }
