param(
  [string]$UserId = "$env:USERDOMAIN\$env:USERNAME"
)

$ErrorActionPreference = 'Stop'

# Staggered AtLogon delays so 2FA dialogs from live accounts don't overlap.
$tasks = @(
  @{ Name = 'IBGateway-isa-live';     Exec = 'wscript.exe'; Arg = '"C:\IBC\Launch-isa-live.vbs"';    Delay = 'PT0S'                 }
  @{ Name = 'IBGateway-isa-paper';    Exec = 'wscript.exe'; Arg = '"C:\IBC\Launch-isa-paper.vbs"';   Delay = 'PT30S'                }
  @{ Name = 'IBGateway-normal-live';  Exec = 'wscript.exe'; Arg = '"C:\IBC\Launch-normal-live.vbs"'; Delay = 'PT60S'                }
  @{ Name = 'IBGateway-normal-paper'; Exec = 'wscript.exe'; Arg = '"C:\IBC\Launch-normal-paper.vbs"';Delay = 'PT90S'                }
  @{ Name = 'FutuOpenDAutoStart';     Exec = 'wscript.exe'; Arg = '"C:\FutuOpenD\LaunchHidden.vbs"'; Delay = 'PT5S'                 }
  @{ Name = 'BrokerWindowsHider';     Exec = 'wscript.exe'; Arg = '"C:\dashboard\deploy\nuc\Launch-Hider.vbs"'; Delay = 'PT15S' }
  @{ Name = 'BrokerTray';             Exec = 'wscript.exe'; Arg = '"C:\dashboard\deploy\nuc\Launch-Tray.vbs"';  Delay = 'PT20S' }
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
