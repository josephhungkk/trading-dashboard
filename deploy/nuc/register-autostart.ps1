param(
  [string]$UserId = "$env:USERDOMAIN\$env:USERNAME"
)

$ErrorActionPreference = 'Stop'

# Staggered AtLogon delays so 2FA dialogs from live accounts don't overlap.
# Cold-boot timing: the prior 0/30/60/90s schedule races IBC's TOTP fill
# against the SecondFactorAuthentication dialog if WG/Java is still warming
# up - leaving isa-live parked at the 2FA prompt. Pushed to 60/120/180/240s
# so the four gateways start AFTER the post-boot stack settles and TOTP
# fills cleanly. Hider follows soon after; Tray is pushed to PT300S so it
# only renders icons after the gateways and sidecars (PT150S) are up.
$tasks = @(
  @{ Name = 'IBGateway-isa-live';     Exec = 'wscript.exe'; Arg = '"C:\IBC\Launch-isa-live.vbs"';    Delay = 'PT60S'                }
  @{ Name = 'IBGateway-isa-paper';    Exec = 'wscript.exe'; Arg = '"C:\IBC\Launch-isa-paper.vbs"';   Delay = 'PT120S'               }
  @{ Name = 'IBGateway-normal-live';  Exec = 'wscript.exe'; Arg = '"C:\IBC\Launch-normal-live.vbs"'; Delay = 'PT180S'               }
  @{ Name = 'IBGateway-normal-paper'; Exec = 'wscript.exe'; Arg = '"C:\IBC\Launch-normal-paper.vbs"';Delay = 'PT240S'               }
  @{ Name = 'FutuOpenDAutoStart';     Exec = 'wscript.exe'; Arg = '"C:\FutuOpenD\LaunchHidden.vbs"'; Delay = 'PT30S'                }
  @{ Name = 'BrokerWindowsHider';     Exec = 'wscript.exe'; Arg = '"C:\dashboard\deploy\nuc\Launch-Hider.vbs"'; Delay = 'PT45S'  }
  @{ Name = 'BrokerTray';             Exec = 'wscript.exe'; Arg = '"C:\dashboard\deploy\nuc\Launch-Tray.vbs"';  Delay = 'PT300S' }
)

# Purge obsolete task names from the previous single-gateway setup.
$obsolete = @('IBGatewayAutoStart')
foreach ($n in $obsolete) {
  if (Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $n -Confirm:$false
    Write-Host ("purged obsolete: " + $n)
  }
}

foreach ($t in $tasks) {
  if (Get-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $t.Name -Confirm:$false
    Write-Host ("replaced: " + $t.Name)
  }
  $action    = New-ScheduledTaskAction -Execute $t.Exec -Argument $t.Arg
  $trigger   = New-ScheduledTaskTrigger -AtLogOn -User $UserId
  $trigger.Delay = $t.Delay
  $settings  = New-ScheduledTaskSettingsSet `
                  -AllowStartIfOnBatteries `
                  -DontStopIfGoingOnBatteries `
                  -StartWhenAvailable `
                  -ExecutionTimeLimit ([TimeSpan]::Zero) `
                  -MultipleInstances IgnoreNew `
                  -Hidden
  $principal = New-ScheduledTaskPrincipal -UserId $UserId -LogonType Interactive -RunLevel Limited
  Register-ScheduledTask -TaskName $t.Name -Action $action -Trigger $trigger -Settings $settings -Principal $principal | Out-Null
  Write-Host ("registered: " + $t.Name + " (delay " + $t.Delay + ")")
}

Write-Host ''
Get-ScheduledTask -TaskName ($tasks | ForEach-Object { $_.Name }) |
  Select-Object TaskName, State,
    @{N='Delay';  E={ $_.Triggers[0].Delay }},
    @{N='Action'; E={ $_.Actions[0].Execute + ' ' + $_.Actions[0].Arguments }} |
  Format-Table -AutoSize
