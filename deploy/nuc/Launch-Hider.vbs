' Launch-Hider.vbs - starts HideBrokerWindows.ps1 with SW_HIDE so the
' powershell console never flashes on the taskbar at logon.
Set sh = CreateObject("WScript.Shell")
sh.Run "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File ""C:\dashboard\deploy\nuc\HideBrokerWindows.ps1"" -TimeoutSec 86400", 0, False
