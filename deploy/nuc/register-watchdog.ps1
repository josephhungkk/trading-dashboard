param(
  [string]$UserId = "$env:USERDOMAIN\$env:USERNAME"
)
$ErrorActionPreference = 'Stop'

$name = 'BrokerWatchdog'
if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
  Unregister-ScheduledTask -TaskName $name -Confirm:$false
  Write-Host ("replaced: $name")
}

$action = New-ScheduledTaskAction -Execute 'wscript.exe' `
  -Argument '"C:\dashboard\deploy\nuc\Launch-Watchdog.vbs"'

# Trigger: at logon + every 5 minutes. RepetitionInterval is on the trigger itself.
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $UserId
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) `
  -RepetitionInterval (New-TimeSpan -Minutes 5) `
  -RepetitionDuration ([TimeSpan]::FromDays(365*10))).Repetition
$trigger.Delay = 'PT3M'   # first run 3 min after logon so initial launches finish

$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -StartWhenAvailable `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 4) `
  -MultipleInstances IgnoreNew `
  -Hidden

$principal = New-ScheduledTaskPrincipal -UserId $UserId -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'Revive any broker whose port is down, every 5 min' | Out-Null
Write-Host "registered: $name (AtLogon+delay 3m, repeat every 5m)"

Get-ScheduledTask -TaskName $name | Select-Object TaskName, State | Format-Table -AutoSize
