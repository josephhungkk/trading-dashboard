# SidecarLib.ps1 - shared pure functions extracted from BrokerWatchdog.ps1 and
# BrokerTray.ps1 so unit tests (deploy/nuc/tests/SidecarLib.Tests.ps1) can
# load + exercise them via Pester without dot-sourcing the full scripts
# (which start Application.Run / a 5-min loop).
#
# Pure: no side effects beyond reading optional input files. All time / file-
# system access is parameterized so tests can inject TestDrive: paths and
# fixed UTC times.

# Returns @($bool, $name) - $true when $Now falls inside any documented IBKR
# reset window. Optional $Now parameter lets tests inject synthetic times.
#
# Sources: IBKR published schedule (Nov 2024).
#   Weekend reset:  Fri 23:00 ET -> Sat 03:00 ET - ALL regions.
#   Daily reset (Sun-Fri):
#     North America: 00:15-01:45 ET
#     Europe:        06:25-07:45 CET (CEST in summer)
#     APAC (HK):     04:45-06:05 HKT  (1st)
#                    20:15-21:15 HKT  (2nd)
function Test-InResetWindow {
    [CmdletBinding()]
    param([DateTime]$Now = (Get-Date).ToUniversalTime())

    $utc = $Now
    if ($utc.Kind -ne [DateTimeKind]::Utc) {
        $utc = $utc.ToUniversalTime()
    }
    try {
        $et  = [System.TimeZoneInfo]::ConvertTimeFromUtc($utc,
                [System.TimeZoneInfo]::FindSystemTimeZoneById('Eastern Standard Time'))
        $cet = [System.TimeZoneInfo]::ConvertTimeFromUtc($utc,
                [System.TimeZoneInfo]::FindSystemTimeZoneById('Central European Standard Time'))
        $hkt = [System.TimeZoneInfo]::ConvertTimeFromUtc($utc,
                [System.TimeZoneInfo]::FindSystemTimeZoneById('China Standard Time'))
    } catch {
        return @($false, 'tz-lookup-failed')
    }

    if (($et.DayOfWeek -eq [DayOfWeek]::Friday   -and $et.Hour -ge 23) -or
        ($et.DayOfWeek -eq [DayOfWeek]::Saturday -and $et.Hour -lt  3)) {
        return @($true, 'weekend')
    }

    $minutesOf = { param($d) $d.Hour * 60 + $d.Minute }

    if ($et.DayOfWeek -ne [DayOfWeek]::Saturday) {
        $m = & $minutesOf $et
        if ($m -ge (0*60+15) -and $m -le (1*60+45)) { return @($true, 'daily-NA') }
    }
    if ($cet.DayOfWeek -ne [DayOfWeek]::Saturday) {
        $m = & $minutesOf $cet
        if ($m -ge (6*60+25) -and $m -le (7*60+45)) { return @($true, 'daily-EU') }
    }
    if ($hkt.DayOfWeek -ne [DayOfWeek]::Saturday) {
        $m = & $minutesOf $hkt
        if ($m -ge (4*60+45) -and $m -le (6*60+ 5)) { return @($true, 'daily-APAC-1') }
        if ($m -ge (20*60+15) -and $m -le (21*60+15)) { return @($true, 'daily-APAC-2') }
    }

    return @($false, '')
}

# Reads <StateDir>/sidecar-<label>.health and returns @{Status; Tip} in the
# BrokerTray vocabulary (up/partial/down/gray). StateDir is parameterized
# so tests can point at TestDrive: temp dirs.
function Read-SidecarHealth {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Label,
        [string]$StateDir = 'C:\dashboard\state'
    )
    $healthFile = Join-Path $StateDir "sidecar-$Label.health"
    if (-not (Test-Path $healthFile)) {
        return @{ Status = 'gray'; Tip = "Sidecar $Label : no health file yet" }
    }
    try {
        $h = Get-Content -Raw $healthFile | ConvertFrom-Json
    } catch {
        return @{ Status = 'down'; Tip = "Sidecar $Label : malformed .health file" }
    }
    $trayStatus = switch ($h.status) {
        'up'       { 'up' }
        'degraded' { 'partial' }
        'down'     { 'down' }
        default    { 'gray' }
    }
    $tip = "Sidecar $Label : $($h.status) (probed $($h.last_probe_at))"
    return @{ Status = $trayStatus; Tip = $tip }
}

# Aggregates two sidecar health files into one tray status:
#   both up                              -> up
#   both down                            -> down
#   gray and no up                       -> gray (not yet probed)
#   anything else (mixed / has partial)  -> partial
function Read-SidecarPair {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string[]]$Labels,
        [Parameter(Mandatory)][string]$Mode,
        [string]$StateDir = 'C:\dashboard\state'
    )
    $sub = $Labels | ForEach-Object { Read-SidecarHealth -Label $_ -StateDir $StateDir }
    $statuses = $sub | ForEach-Object { $_.Status }
    $up = @($statuses | Where-Object { $_ -eq 'up' }).Count
    $down = @($statuses | Where-Object { $_ -eq 'down' }).Count
    $gray = @($statuses | Where-Object { $_ -eq 'gray' }).Count

    $rollup = if ($up -eq $Labels.Count) { 'up' }
              elseif ($down -eq $Labels.Count) { 'down' }
              elseif ($gray -gt 0 -and $up -eq 0) { 'gray' }
              else { 'partial' }

    $detail = for ($i = 0; $i -lt $Labels.Count; $i++) {
        "{0}={1}" -f $Labels[$i], $statuses[$i]
    }
    return @{ Status = $rollup; Tip = ("Sidecar {0}: {1}" -f $Mode, ($detail -join ' ')) }
}
