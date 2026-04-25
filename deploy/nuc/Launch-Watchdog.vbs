' Launch-Watchdog.vbs - starts BrokerWatchdog.ps1 with SW_HIDE so the
' powershell console never flashes on the taskbar during the 5-minute poll.
Set sh = CreateObject("WScript.Shell")
sh.Run "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File ""C:\dashboard\deploy\nuc\BrokerWatchdog.ps1""", 0, False
