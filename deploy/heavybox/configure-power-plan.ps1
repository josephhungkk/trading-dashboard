# deploy/heavybox/configure-power-plan.ps1 — Phase 11a-A2
# Run as Administrator on the heavy AI box. Activates Balanced plan,
# disables Windows' own sleep timer (our scheduled task install-idle-
# suspend.ps1 owns that), disables hibernate (suspend-to-RAM is faster
# for WoL wake than hibernate-to-disk), and forces Wake-on-Magic-Packet
# on the active NIC so WoL actually works.

#Requires -RunAsAdministrator
$ErrorActionPreference = "Stop"

Write-Host "==> Activating Balanced power plan..."
$balancedGuid = "381b4222-f694-41f0-9685-ff5bb260df2e"
& powercfg /setactive $balancedGuid

Write-Host "==> Disabling Windows' own sleep timer (our scheduled task owns it)..."
& powercfg /change standby-timeout-ac 0
& powercfg /change hibernate-timeout-ac 0
& powercfg /change disk-timeout-ac 0
& powercfg /change monitor-timeout-ac 1

Write-Host "==> Disabling USB selective suspend..."
& powercfg /setacvalueindex SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 48e6b7a6-50f5-4782-a5d4-53bb8f07e226 0
& powercfg /setactive SCHEME_CURRENT

Write-Host "==> Disabling PCIe Link State Power Management (GPU + NIC)..."
& powercfg /setacvalueindex SCHEME_CURRENT 501a4d13-42af-4429-9fd1-a8218c268e20 ee12f906-d277-404b-b6da-e5fa1a576df5 0
& powercfg /setactive SCHEME_CURRENT

Write-Host "==> Enabling wake timers (required for scheduled WoL responses)..."
& powercfg /setacvalueindex SCHEME_CURRENT 238c9fa8-0aad-41ed-83f4-97be242c8f20 bd3b718a-0680-4d9d-8ab2-e1d2b4ac806d 1
& powercfg /setactive SCHEME_CURRENT

Write-Host "==> Enabling Wake-on-Magic-Packet on all active NICs..."
# This is THE load-bearing setting for WoL. Without it, the magic packet
# is silently dropped by the NIC and the box never wakes.
$nics = Get-NetAdapter | Where-Object { $_.Status -eq "Up" }
foreach ($nic in $nics) {
    Write-Host "  - $($nic.Name) ($($nic.InterfaceDescription))"
    $magic = Get-NetAdapterAdvancedProperty -Name $nic.Name -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -match "Wake.*Magic" }
    if ($magic) {
        Set-NetAdapterAdvancedProperty -Name $nic.Name `
            -DisplayName $magic[0].DisplayName -DisplayValue "Enabled" -NoRestart
        Write-Host "    Wake on Magic Packet -> Enabled"
    } else {
        Write-Host "    (no Wake-on-Magic-Packet property — NIC may not support it)"
    }

    # Allow this device to wake the computer (separate flag)
    & powercfg /deviceenablewake "$($nic.InterfaceDescription)" 2>$null
}

Write-Host "==> Verifying NICs reporting wake capability..."
& powercfg /devicequery wake_armed

Write-Host "OK Heavy-box power plan configured. Active scheme:"
& powercfg /getactivescheme
