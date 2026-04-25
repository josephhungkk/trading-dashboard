# Pester tests for deploy/nuc/lib/SidecarLib.ps1.
# Run via deploy/nuc/tests/Run-Tests.ps1 or:
#   Invoke-Pester -Path deploy/nuc/tests/SidecarLib.Tests.ps1
#
# Pester 5+ syntax (BeforeAll, Should -Be). Pester 5 ships separately from
# Windows PowerShell 5.1's bundled Pester 3.4; Run-Tests.ps1 ensures v5 is
# installed before invoking.

BeforeAll {
    $libPath = Join-Path $PSScriptRoot '..\lib\SidecarLib.ps1'
    . (Resolve-Path $libPath).Path

    # Helper used by Read-SidecarPair tests. Defined at file scope (not
    # inside Describe) because Pester 5 scoping isolates Describe-local
    # functions from It blocks.
    function Write-Health {
        param([string]$Dir, [string]$Label, [string]$Status)
        $body = ('{{"label":"{0}","status":"{1}","last_probe_at":"2026-04-25T22:00:00Z","probe_output":""}}' -f $Label, $Status)
        Set-Content -Path (Join-Path $Dir "sidecar-$Label.health") -Value $body
    }
}

Describe 'Test-InResetWindow' {
    It 'returns false outside any reset window (Tuesday 14:00 UTC)' {
        # Tuesday afternoon UTC -> 09:00 ET (no reset), 15:00 CET (no reset),
        # 22:00 HKT (between APAC windows).
        $now = [DateTime]::SpecifyKind('2026-04-21T14:00:00', [DateTimeKind]::Utc)
        $r = Test-InResetWindow -Now $now
        $r[0] | Should -BeFalse
        $r[1] | Should -Be ''
    }

    It 'returns true (weekend) inside Friday 23:30 ET window' {
        # Friday 23:30 ET = Saturday 03:30 UTC (EST = UTC-5).
        $now = [DateTime]::SpecifyKind('2026-04-25T03:30:00', [DateTimeKind]::Utc)
        $r = Test-InResetWindow -Now $now
        $r[0] | Should -BeTrue
        $r[1] | Should -Be 'weekend'
    }

    It 'returns true (weekend) just past midnight Saturday ET' {
        # Saturday 00:30 ET = Saturday 04:30 UTC (EST).
        $now = [DateTime]::SpecifyKind('2026-04-25T04:30:00', [DateTimeKind]::Utc)
        $r = Test-InResetWindow -Now $now
        $r[0] | Should -BeTrue
        $r[1] | Should -Be 'weekend'
    }

    It 'returns true (daily-NA) inside 00:30 ET on Tuesday' {
        # Tuesday 00:30 ET = Tuesday 04:30 UTC (EST = UTC-5).
        # Note: Eastern Standard Time on Windows is fixed UTC-5 (no DST applied
        # by the simple Id; FindSystemTimeZoneById('Eastern Standard Time')
        # returns the EST tz which the watchdog accepts as the reference).
        $now = [DateTime]::SpecifyKind('2026-04-21T05:30:00', [DateTimeKind]::Utc)
        $r = Test-InResetWindow -Now $now
        $r[0] | Should -BeTrue
        $r[1] | Should -Be 'daily-NA'
    }

    It 'precedence: NA wins over EU when both windows overlap' {
        # The NA daily window in UTC (05:15-06:45 in winter, 04:15-05:45 in
        # DST) fully contains the EU daily window (05:25-06:45 / 04:25-05:45),
        # so daily-EU is shadowed by daily-NA whenever both fire. This codifies
        # the existing function's first-match precedence: NA > EU > APAC.
        $now = [DateTime]::SpecifyKind('2026-04-22T05:30:00', [DateTimeKind]::Utc)
        $r = Test-InResetWindow -Now $now
        $r[0] | Should -BeTrue
        $r[1] | Should -Be 'daily-NA'
    }

    It 'returns true (daily-APAC-1) inside 05:00 HKT on Thursday' {
        # Thursday 05:00 HKT = Wednesday 21:00 UTC (HKT = UTC+8).
        $now = [DateTime]::SpecifyKind('2026-04-22T21:00:00', [DateTimeKind]::Utc)
        $r = Test-InResetWindow -Now $now
        $r[0] | Should -BeTrue
        $r[1] | Should -Be 'daily-APAC-1'
    }

    It 'returns true (daily-APAC-2) inside 20:30 HKT on Wednesday' {
        # Wednesday 20:30 HKT = Wednesday 12:30 UTC.
        $now = [DateTime]::SpecifyKind('2026-04-22T12:30:00', [DateTimeKind]::Utc)
        $r = Test-InResetWindow -Now $now
        $r[0] | Should -BeTrue
        $r[1] | Should -Be 'daily-APAC-2'
    }

    It 'returns false on Saturday outside the weekend window (Sat 12:00 ET)' {
        # Saturday daily resets are SKIPPED in all regions.
        # Saturday 12:00 ET = Saturday 17:00 UTC.
        $now = [DateTime]::SpecifyKind('2026-04-25T17:00:00', [DateTimeKind]::Utc)
        $r = Test-InResetWindow -Now $now
        $r[0] | Should -BeFalse
    }

    It 'accepts a non-UTC DateTime by converting to UTC internally' {
        # Same instant as the precedence test, but expressed as Local time.
        # Result must be the same regardless of input Kind.
        $localNow = [DateTime]::SpecifyKind('2026-04-22T05:30:00', [DateTimeKind]::Utc).ToLocalTime()
        $r = Test-InResetWindow -Now $localNow
        $r[0] | Should -BeTrue
        $r[1] | Should -Be 'daily-NA'
    }
}

Describe 'Read-SidecarHealth' {
    BeforeEach {
        $script:stateDir = New-Item -ItemType Directory -Path (Join-Path $TestDrive ([Guid]::NewGuid())) -Force
    }

    It 'returns gray when the .health file is missing' {
        $r = Read-SidecarHealth -Label 'isa-paper' -StateDir $script:stateDir
        $r.Status | Should -Be 'gray'
        $r.Tip    | Should -Match 'no health file yet'
    }

    It 'returns up when status=up' {
        $body = '{"label":"isa-paper","status":"up","last_probe_at":"2026-04-25T22:00:00Z","probe_output":""}'
        Set-Content -Path (Join-Path $script:stateDir 'sidecar-isa-paper.health') -Value $body
        $r = Read-SidecarHealth -Label 'isa-paper' -StateDir $script:stateDir
        $r.Status | Should -Be 'up'
        $r.Tip    | Should -Match 'isa-paper : up'
    }

    It 'returns partial when status=degraded' {
        $body = '{"label":"isa-live","status":"degraded","last_probe_at":"2026-04-25T22:00:00Z","probe_output":""}'
        Set-Content -Path (Join-Path $script:stateDir 'sidecar-isa-live.health') -Value $body
        $r = Read-SidecarHealth -Label 'isa-live' -StateDir $script:stateDir
        $r.Status | Should -Be 'partial'
    }

    It 'returns down when status=down' {
        $body = '{"label":"normal-live","status":"down","last_probe_at":"2026-04-25T22:00:00Z","probe_output":""}'
        Set-Content -Path (Join-Path $script:stateDir 'sidecar-normal-live.health') -Value $body
        $r = Read-SidecarHealth -Label 'normal-live' -StateDir $script:stateDir
        $r.Status | Should -Be 'down'
    }

    It 'returns down with a malformed-file tip when JSON is invalid' {
        Set-Content -Path (Join-Path $script:stateDir 'sidecar-isa-paper.health') -Value 'not-json{'
        $r = Read-SidecarHealth -Label 'isa-paper' -StateDir $script:stateDir
        $r.Status | Should -Be 'down'
        $r.Tip    | Should -Match 'malformed'
    }

    It 'returns gray when the JSON has an unexpected status value' {
        $body = '{"label":"isa-paper","status":"weird","last_probe_at":"2026-04-25T22:00:00Z","probe_output":""}'
        Set-Content -Path (Join-Path $script:stateDir 'sidecar-isa-paper.health') -Value $body
        $r = Read-SidecarHealth -Label 'isa-paper' -StateDir $script:stateDir
        $r.Status | Should -Be 'gray'
    }
}

Describe 'Read-SidecarPair' {
    BeforeEach {
        $script:stateDir = New-Item -ItemType Directory -Path (Join-Path $TestDrive ([Guid]::NewGuid())) -Force
    }

    It 'rolls up to up when both sidecars are up' {
        Write-Health $script:stateDir 'isa-live' 'up'
        Write-Health $script:stateDir 'normal-live' 'up'
        $r = Read-SidecarPair -Labels @('isa-live','normal-live') -Mode 'Live' -StateDir $script:stateDir
        $r.Status | Should -Be 'up'
        $r.Tip    | Should -Match 'Live'
    }

    It 'rolls up to down when both sidecars are down' {
        Write-Health $script:stateDir 'isa-paper' 'down'
        Write-Health $script:stateDir 'normal-paper' 'down'
        $r = Read-SidecarPair -Labels @('isa-paper','normal-paper') -Mode 'Paper' -StateDir $script:stateDir
        $r.Status | Should -Be 'down'
    }

    It 'rolls up to partial when one is up and one is down' {
        Write-Health $script:stateDir 'isa-live' 'up'
        Write-Health $script:stateDir 'normal-live' 'down'
        $r = Read-SidecarPair -Labels @('isa-live','normal-live') -Mode 'Live' -StateDir $script:stateDir
        $r.Status | Should -Be 'partial'
    }

    It 'rolls up to gray when neither has been probed yet' {
        # No .health files written -> Read-SidecarHealth returns gray for each.
        $r = Read-SidecarPair -Labels @('isa-live','normal-live') -Mode 'Live' -StateDir $script:stateDir
        $r.Status | Should -Be 'gray'
    }

    It 'rolls up to partial when one is up and one is gray' {
        Write-Health $script:stateDir 'isa-paper' 'up'
        # normal-paper has no .health file -> gray
        $r = Read-SidecarPair -Labels @('isa-paper','normal-paper') -Mode 'Paper' -StateDir $script:stateDir
        $r.Status | Should -Be 'partial'
    }

    It 'tip lists per-label sub-statuses for diagnostics' {
        Write-Health $script:stateDir 'isa-live' 'up'
        Write-Health $script:stateDir 'normal-live' 'down'
        $r = Read-SidecarPair -Labels @('isa-live','normal-live') -Mode 'Live' -StateDir $script:stateDir
        $r.Tip | Should -Match 'isa-live=up'
        $r.Tip | Should -Match 'normal-live=down'
    }
}
