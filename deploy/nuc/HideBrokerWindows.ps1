param(
  [int]$TimeoutSec = 120
)

# Polls for FutuOpenD + IBKR Gateway windows, strips them from the taskbar
# (WS_EX_TOOLWINDOW, clear WS_EX_APPWINDOW) and hides them (SW_HIDE).
# Run once per logon, shortly after the broker-launch tasks fire.

Add-Type @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public class W {
    public delegate bool EnumDelegate(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern int  GetWindowLong(IntPtr hWnd, int nIndex);
    [DllImport("user32.dll")] public static extern int  SetWindowLong(IntPtr hWnd, int nIndex, int dwNewLong);
    [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter,
                                                                     int X, int Y, int cx, int cy, uint uFlags);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumDelegate lpEnumFunc, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern IntPtr GetParent(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr hWnd);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder s, int n);
}
"@

$GWL_EXSTYLE       = -20
$WS_EX_TOOLWINDOW  = 0x00000080
$WS_EX_APPWINDOW   = 0x00040000
$SWP_NOSIZE        = 0x0001
$SWP_NOMOVE        = 0x0002
$SWP_NOZORDER      = 0x0004
$SWP_FRAMECHANGED  = 0x0020
$SWP_HIDEWINDOW    = 0x0080
$SW_HIDE           = 0

# Title-only matchers. CRITICAL: do NOT fall back to process-name, because
# IBKR Gateway's 2FA dialog is spawned by the same java.exe and would be hidden
# mid-SendKeys, breaking the TOTP filler. The 2FA dialog title is
# "Second Factor Authentication" which must not match anything here.
$targets = [ordered]@{
  'FutuOpenD'    = @{ TitleRegex = '^C:\\FutuOpenD\\FutuOpenD\.exe$' }
  'IBKRGateway'  = @{ TitleRegex = '^IBKR Gateway$|^IB Gateway$' }
}

$deadline = (Get-Date).AddSeconds($TimeoutSec)
$seenHandles = @{}

# Collects all top-level windows whose title matches any target regex.
# Uses EnumWindows, not Get-Process MainWindowHandle, because IBKR Gateway
# has multiple top-level SunAwtFrame windows per process and only one is
# reported as "main" at any given moment.
function Get-BrokerWindows {
  $hits = New-Object System.Collections.Generic.List[object]
  $cb = [W+EnumDelegate]{
    param($h, $l)
    if ([W]::GetParent($h) -ne [IntPtr]::Zero) { return $true }
    $len = [W]::GetWindowTextLength($h); if ($len -eq 0) { return $true }
    $sb = New-Object System.Text.StringBuilder ($len + 1)
    [W]::GetWindowText($h, $sb, $sb.Capacity) | Out-Null
    $title = $sb.ToString()
    foreach ($k in @($targets.Keys)) {
      if ($title -match $targets[$k].TitleRegex) {
        $hits.Add([pscustomobject]@{ Label=$k; Hwnd=$h; Title=$title })
        break
      }
    }
    return $true
  }
  [W]::EnumWindows($cb, [IntPtr]::Zero) | Out-Null
  return $hits
}

while ((Get-Date) -lt $deadline) {
  foreach ($w in Get-BrokerWindows) {
    $h = $w.Hwnd
    $ex = [W]::GetWindowLong($h, $GWL_EXSTYLE)
    $taskbarOk = (($ex -band $WS_EX_TOOLWINDOW) -ne 0) -and (($ex -band $WS_EX_APPWINDOW) -eq 0)
    $visible   = [W]::IsWindowVisible($h)
    # Skip only if fully quiet: taskbar-cleansed AND currently invisible AND previously handled.
    if ($taskbarOk -and -not $visible -and $seenHandles.ContainsKey($h)) { continue }
    if (-not $taskbarOk) {
      $new = ($ex -band (-bnot $WS_EX_APPWINDOW)) -bor $WS_EX_TOOLWINDOW
      [W]::SetWindowLong($h, $GWL_EXSTYLE, $new) | Out-Null
      [W]::SetWindowPos($h, [IntPtr]::Zero, 0,0,0,0,
                         ($SWP_NOSIZE -bor $SWP_NOMOVE -bor $SWP_NOZORDER -bor
                          $SWP_FRAMECHANGED -bor $SWP_HIDEWINDOW)) | Out-Null
    }
    if ($visible) { [W]::ShowWindow($h, $SW_HIDE) | Out-Null }
    $action = if ($seenHandles.ContainsKey($h)) { 're-hidden' } else { 'hidden' }
    $seenHandles[$h] = $true
    Write-Host ("{0}: {1} hwnd={2} title='{3}'" -f $action, $w.Label, $h, $w.Title)
  }
  Start-Sleep -Milliseconds 1000
}

Write-Host ("done after {0}s, hid {1} distinct windows" -f $TimeoutSec, $seenHandles.Count)
