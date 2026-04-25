param(
  [string]$UserId = "$env:USERDOMAIN\$env:USERNAME",
  [string]$Time   = '23:50'   # London local (NUC is GMT/BST)
)
$ErrorActionPreference = 'Stop'

$name = 'BrokerDailyRestart'
if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
  Unregister-ScheduledTask -TaskName $name -Confirm:$false
  Write-Host ("replaced: " + $name)
}

$action    = New-ScheduledTaskAction -Execute 'wscript.exe' `
               -Argument '"C:\dashboard\deploy\nuc\Launch-DailyRestart.vbs"'
$trigger   = New-ScheduledTaskTrigger -Daily -At ([datetime]::ParseExact($Time, 'HH:mm', $null))
$settings  = New-ScheduledTaskSettingsSet `
               -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
               -StartWhenAvailable `
               -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
               -MultipleInstances IgnoreNew `
               -Hidden
$principal = New-ScheduledTaskPrincipal -UserId $UserId -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger `
  -Settings $settings -Principal $principal `
  -Description 'Daily full restart of all broker endpoints — kills all 5, refires at-logon tasks with boot-style stagger' | Out-Null
Write-Host ("registered: " + $name + " (daily at " + $Time + " local)")

Get-ScheduledTask -TaskName $name |
  Select-Object TaskName, State,
    @{N='NextRun'; E={ ($_ | Get-ScheduledTaskInfo).NextRunTime }},
    @{N='Action';  E={ $_.Actions[0].Execute + ' ' + $_.Actions[0].Arguments }} |
  Format-Table -AutoSize
