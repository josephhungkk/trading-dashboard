' Launch-Tray.vbs - starts BrokerTray.ps1 with SW_HIDE so the powershell
' console never flashes on the taskbar at logon. The NotifyIcon tray icons
' themselves remain visible in the notification area as intended.
Set sh = CreateObject("WScript.Shell")
sh.Run "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File ""C:\dashboard\deploy\nuc\BrokerTray.ps1""", 0, False
