#
# restart-tray.ps1 — kill every BrokerTray process (including orphaned
# detached launchers the scheduled-task Stop doesn't reach) and re-
# start the tray scheduled task. Idempotent.
#
# Run as Administrator:
#   powershell -ExecutionPolicy Bypass -File .\restart-tray.ps1
#
[CmdletBinding()] param()
$ErrorActionPreference = 'Continue'

$currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
$admin = (New-Object Security.Principal.WindowsPrincipal $currentUser).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Write-Error 'Must run as Administrator.'; return }

Write-Host '-> Stopping + disabling BrokerTray scheduled task (so nothing re-fires mid-kill)'
& schtasks.exe /End /TN 'BrokerTray' 2>$null | Out-Null
Stop-ScheduledTask  -TaskName 'BrokerTray' -ErrorAction SilentlyContinue

Write-Host ''
Write-Host '-> Killing every process that looks like a tray instance'
# Two launch paths to sweep:
#   1. wscript.exe running Launch-Tray.vbs (the outer hidden launcher)
#   2. powershell.exe running BrokerTray.ps1 (the actual tray script)
# NotifyIcon sticks in the tray until the OWNING PowerShell process
# fully exits — duplicates appear when multiple PS hosts are alive.
$killed = 0
$procs = Get-CimInstance Win32_Process -Filter "Name='wscript.exe' OR Name='powershell.exe'" -ErrorAction SilentlyContinue
foreach ($p in $procs) {
    if (-not $p.CommandLine) { continue }
    if ($p.CommandLine -match 'Launch-Tray\.vbs' -or
        $p.CommandLine -match 'BrokerTray\.ps1') {
        Write-Host ("  killing {0} pid={1}" -f $p.Name, $p.ProcessId)
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        $killed++
    }
}
Write-Host ("  killed {0} process(es)" -f $killed)

# Phantom NotifyIcons linger until explorer.exe's tray area refreshes.
# Windows does this lazily on mouse-hover; force it now so duplicate
# icons disappear immediately.
Write-Host ''
Write-Host '-> Refreshing system tray (clears stale NotifyIcon entries)'
$sig = @'
using System;
using System.Runtime.InteropServices;
public class TrayRefresh {
    [DllImport("user32.dll")] public static extern IntPtr FindWindow(string c, string w);
    [DllImport("user32.dll")] public static extern IntPtr FindWindowEx(IntPtr p, IntPtr x, string c, string w);
    [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr h, int msg, IntPtr w, IntPtr l);
    [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out Rect r);
    public struct Rect { public int L, T, R, B; }
    public static void Refresh() {
        // Walk down the tray container chain and fire mouse-moves over
        // every pixel — this is the standard Windows-shell incantation
        // to force the notification area to re-enumerate its icons
        // and drop entries whose owning process is gone.
        IntPtr h = FindWindow("Shell_TrayWnd", null);
        h = FindWindowEx(h, IntPtr.Zero, "TrayNotifyWnd", null);
        h = FindWindowEx(h, IntPtr.Zero, "SysPager", null);
        h = FindWindowEx(h, IntPtr.Zero, "ToolbarWindow32", null);
        if (h == IntPtr.Zero) return;
        Rect r; GetClientRect(h, out r);
        for (int x = 0; x < r.R; x += 5) {
            for (int y = 0; y < r.B; y += 5) {
                SendMessage(h, 0x0200 /* WM_MOUSEMOVE */, IntPtr.Zero, (IntPtr)((y << 16) | x));
            }
        }
    }
}
'@
try {
    Add-Type -TypeDefinition $sig -ErrorAction SilentlyContinue
    [TrayRefresh]::Refresh()
} catch {
    Write-Host ("  (tray refresh skipped: {0})" -f $_.Exception.Message) -ForegroundColor DarkGray
}

Start-Sleep -Milliseconds 300

Write-Host ''
Write-Host '-> Re-enabling + firing BrokerTray'
Enable-ScheduledTask -TaskName 'BrokerTray' -ErrorAction SilentlyContinue | Out-Null
Start-ScheduledTask  -TaskName 'BrokerTray' -ErrorAction SilentlyContinue

Start-Sleep -Seconds 2

Write-Host ''
Write-Host '-> Post-restart check'
$after = Get-CimInstance Win32_Process -Filter "Name='wscript.exe' OR Name='powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'Launch-Tray\.vbs|BrokerTray\.ps1' }
if ($after) {
    foreach ($p in $after) {
        Write-Host ("  running: {0} pid={1}" -f $p.Name, $p.ProcessId)
    }
    $count = @($after).Count
    Write-Host ("  ({0} process(es) alive — expect 1 wscript + 1 powershell)" -f $count)
} else {
    Write-Host '  WARN: no tray process found after restart — check Task Scheduler history for BrokerTray'
}
Write-Host ''
Write-Host 'Done.' -ForegroundColor Green
