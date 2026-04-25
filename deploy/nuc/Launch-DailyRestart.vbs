' Launch-DailyRestart.vbs - runs the 23:50 daily-restart script with SW_HIDE.
Set sh = CreateObject("WScript.Shell")
sh.Run "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File ""C:\dashboard\deploy\nuc\DailyRestart.ps1""", 0, False
