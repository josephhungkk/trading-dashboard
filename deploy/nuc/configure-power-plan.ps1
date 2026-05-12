# deploy/nuc/configure-power-plan.ps1 — Phase 11a-A2
# Run as Administrator on the NUC15PRO. Activates High Performance
# plan and disables every form of sleep/hibernate/disk-timeout because
# the NUC hosts 24/7 services (PG-18, Redis, broker sidecars, BE
# container, NUC-side Ollama 7-8B).
#
# Pairs with install-ollama.ps1 + install-wol-helper.ps1; run once
# per host after first deploy. Safe to re-run.

#Requires -RunAsAdministrator
$ErrorActionPreference = "Stop"

Write-Host "==> Activating High Performance power plan..."
$highPerfGuid = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
& powercfg /setactive $highPerfGuid

Write-Host "==> Disabling sleep / hibernate / disk-timeout on AC..."
& powercfg /change standby-timeout-ac 0
& powercfg /change hibernate-timeout-ac 0
& powercfg /change disk-timeout-ac 0
& powercfg /change monitor-timeout-ac 5

Write-Host "==> Disabling USB selective suspend (TWS dongles + sidecars)..."
# USB Suspend subgroup GUID + USBSettings subgroup
& powercfg /setacvalueindex SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 48e6b7a6-50f5-4782-a5d4-53bb8f07e226 0
& powercfg /setactive SCHEME_CURRENT

Write-Host "==> Disabling PCIe Link State Power Management..."
& powercfg /setacvalueindex SCHEME_CURRENT 501a4d13-42af-4429-9fd1-a8218c268e20 ee12f906-d277-404b-b6da-e5fa1a576df5 0
& powercfg /setactive SCHEME_CURRENT

Write-Host "==> Pinning processor state to 100% min/max..."
& powercfg /setacvalueindex SCHEME_CURRENT 54533251-82be-4824-96c1-47b60b740d00 893dee8e-2bef-41e0-89c6-b55d0929964c 100
& powercfg /setacvalueindex SCHEME_CURRENT 54533251-82be-4824-96c1-47b60b740d00 bc5038f7-23e0-4960-96da-33abaf5935ec 100
& powercfg /setactive SCHEME_CURRENT

Write-Host "OK NUC power plan configured. Active scheme:"
& powercfg /getactivescheme
