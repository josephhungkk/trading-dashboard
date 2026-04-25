# Post-install hardening for FutuOpenD + Ollama.
#
# Run once after installing (or re-installing) either product. Idempotent —
# safe to re-run on every NUC rebuild or installer upgrade. Requires admin.
#
# What it does:
#   1. FutuOpenD firewall — ensures a WG-scoped rule on 11111 exists, then
#      disables every other inbound rule that matches FutuOpenD. The Futu
#      installer creates very permissive "Any remote" rules that would expose
#      the port to the LAN.
#   2. Ollama firewall — same pattern on 11434. The Ollama installer also
#      creates "Any remote" rules.
#   3. Ollama env — sets OLLAMA_KEEP_ALIVE=-1 at Machine scope so models stay
#      loaded between requests (the default 5-minute idle unload causes
#      seconds-long cold-starts on every tier=light chat).
#   4. Ollama models — pulls the tier model if missing. -Tier light (default)
#      pulls llama3.1:8b-instruct-q4_K_M (NUC), -Tier heavy pulls
#      qwen2.5:32b-instruct-q4_K_M (~19GB, for the heavy AI box),
#      -Tier both pulls both. These tags are what
#      backend/scripts/seed_config.py seeds as ollama.light_model /
#      ollama.heavy_model; the adapter 404s if the corresponding tag is
#      missing. Skip entirely with -SkipModels.
#   5. Ollama service (opt-in via -InstallService) — registers a scheduled
#      task "OllamaServer" that runs `ollama.exe serve` as SYSTEM at system
#      startup, with restart-on-failure. Solves the "Ollama isn't running
#      unless somebody's logged in and started the tray app" gap on shared
#      machines (e.g. the heavy box, where multiple users may log in but we
#      want Ollama reachable whenever the box is on). Uses Task Scheduler
#      rather than a native Windows Service because `ollama.exe serve` is
#      a plain console app — it doesn't implement the ServiceCtrlDispatcher
#      protocol, so `New-Service` + `Start-Service` fails with "Cannot
#      start service". Task Scheduler runs the process directly without
#      expecting service protocol compliance. Pins the current user's
#      models dir via Machine-scope OLLAMA_MODELS so the SYSTEM-run task
#      finds existing pulls without a ~20 GB re-download.
#
# Run (typical):
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\harden-post-install.ps1
#
# Heavy AI box (pulls 32B model + installs service):
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\harden-post-install.ps1 -Tier heavy -InstallService

param(
  [switch]$SkipModels,
  # Which Ollama model(s) to ensure are pulled. The NUC runs the tier=light
  # model, the heavy AI box runs the tier=heavy model, so each machine gets
  # the right default when run locally. Use -Tier both on a dev box you want
  # to serve both tiers from.
  [ValidateSet('light','heavy','both')]
  [string]$Tier = 'light',
  # Install Ollama as a Windows service (LocalSystem, auto-start at boot).
  # Opt-in: most machines don't need it because the stock "ollama app.exe"
  # in the user's tray handles lifecycle. Enable on multi-user hosts (e.g.
  # the heavy box) where you want Ollama reachable regardless of who is
  # logged in.
  [switch]$InstallService
)

$ErrorActionPreference = 'Continue'

function Require-Admin {
  $me = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
  if (-not $me.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "ERROR: this script must run elevated (right-click -> Run as Administrator)" -ForegroundColor Red
    exit 1
  }
}

function Ensure-FirewallRule {
  # Create an inbound TCP allow rule scoped to 10.10.0.0/24 if it doesn't exist.
  param([string]$Name, [int]$Port)
  $existing = Get-NetFirewallRule -DisplayName $Name -ErrorAction SilentlyContinue
  if ($existing) {
    # Re-enable if someone disabled it; verify scope.
    $existing | Enable-NetFirewallRule -ErrorAction SilentlyContinue
    Write-Host ("exists: {0}" -f $Name)
    return
  }
  New-NetFirewallRule -DisplayName $Name `
    -Direction Inbound -Protocol TCP -LocalPort $Port `
    -RemoteAddress 10.10.0.0/24 -Action Allow | Out-Null
  Write-Host ("created: {0} (TCP {1}, 10.10.0.0/24)" -f $Name, $Port)
}

function Disable-PermissiveRules {
  # Disable every inbound rule matching any of $NamePatterns EXCEPT one named
  # exactly $KeepName. Idempotent — disabling an already-disabled rule is a
  # no-op in NetSecurity.
  param([string[]]$NamePatterns, [string]$KeepName)
  $rules = foreach ($p in $NamePatterns) {
    Get-NetFirewallRule -DisplayName $p -ErrorAction SilentlyContinue
  }
  # Dedup by Name (Get-NetFirewallRule can return duplicates across stores).
  $rules = $rules | Sort-Object -Property Name -Unique
  foreach ($r in $rules) {
    if ($r.DisplayName -eq $KeepName) { continue }
    if ($r.Enabled -eq 'False') { continue }
    Disable-NetFirewallRule -Name $r.Name -ErrorAction SilentlyContinue
    Write-Host ("disabled: {0} [{1}]" -f $r.DisplayName, $r.Name)
  }
}

Require-Admin

# Role-detect by binary presence so the same script works on the NUC (Futu +
# Ollama), the heavy AI box (Ollama only), or any future host — no flag needed.
$hasFutu = (Test-Path 'C:\FutuOpenD\FutuOpenD.exe') -or
           (@(Get-Process -Name 'FutuOpenD' -ErrorAction SilentlyContinue).Count -gt 0)
$hasOllama = $null -ne (Get-Command ollama -ErrorAction SilentlyContinue)

Write-Host "==== 1. FutuOpenD firewall ===="
if ($hasFutu) {
  Ensure-FirewallRule -Name 'FutuOpenD WireGuard' -Port 11111
  Disable-PermissiveRules -NamePatterns @('FutuOpenD','futuopend') -KeepName 'FutuOpenD WireGuard'
} else {
  Write-Host "skipped — no FutuOpenD install detected on this host"
  # Clean up a stale 'FutuOpenD WireGuard' rule from a prior mis-run on this host.
  $stale = Get-NetFirewallRule -DisplayName 'FutuOpenD WireGuard' -ErrorAction SilentlyContinue
  if ($stale) {
    Remove-NetFirewallRule -Name $stale.Name -ErrorAction SilentlyContinue
    Write-Host "cleaned up stale 'FutuOpenD WireGuard' rule (left over from earlier run)"
  }
}

Write-Host ""
Write-Host "==== 2. Ollama firewall ===="
if ($hasOllama) {
  Ensure-FirewallRule -Name 'Ollama WireGuard' -Port 11434
  Disable-PermissiveRules -NamePatterns @('Ollama','ollama') -KeepName 'Ollama WireGuard'
} else {
  Write-Host "skipped — no Ollama install detected on this host"
}

Write-Host ""
Write-Host "==== 3. Ollama environment ===="
$current = [Environment]::GetEnvironmentVariable('OLLAMA_KEEP_ALIVE','Machine')
if ($current -ne '-1') {
  [Environment]::SetEnvironmentVariable('OLLAMA_KEEP_ALIVE', '-1', 'Machine')
  Write-Host "set OLLAMA_KEEP_ALIVE=-1 (was '$current')"
  Write-Host "NOTE: kill ollama.exe (Task Manager or 'Get-Process ollama | Stop-Process -Force') so ollama app respawns it with the new env."
} else {
  Write-Host "OLLAMA_KEEP_ALIVE=-1 already set"
}
$hostv = [Environment]::GetEnvironmentVariable('OLLAMA_HOST','Machine')
if ($hostv -ne '0.0.0.0:11434' -and $hostv -ne '0.0.0.0') {
  [Environment]::SetEnvironmentVariable('OLLAMA_HOST', '0.0.0.0:11434', 'Machine')
  Write-Host "set OLLAMA_HOST=0.0.0.0:11434 (was '$hostv')"
} else {
  Write-Host "OLLAMA_HOST already listens on all interfaces ('$hostv')"
}

if ($SkipModels) {
  Write-Host ""
  Write-Host "==== 4. Ollama models (SKIPPED via -SkipModels) ===="
} else {
  Write-Host ""
  Write-Host "==== 4. Ollama models ===="
  $ollama = Get-Command ollama -ErrorAction SilentlyContinue
  if (-not $ollama) {
    Write-Host "WARN: ollama.exe not on PATH; skipping model pull"
  } else {
    $required = switch ($Tier) {
      'light' { @('llama3.1:8b-instruct-q4_K_M') }
      'heavy' { @('qwen2.5:32b-instruct-q4_K_M') }
      'both'  { @('llama3.1:8b-instruct-q4_K_M','qwen2.5:32b-instruct-q4_K_M') }
    }
    Write-Host ("tier={0} required=[{1}]" -f $Tier, ($required -join ', '))
    foreach ($m in $required) {
      $present = $false
      try {
        $tags = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 5
        $present = @($tags.models | Where-Object { $_.name -eq $m }).Count -gt 0
      } catch {}
      if ($present) {
        Write-Host "present: $m"
      } else {
        Write-Host "pulling: $m (this takes several minutes)"
        & ollama pull $m
        if ($LASTEXITCODE -eq 0) { Write-Host "pulled:  $m" }
        else                     { Write-Host "FAILED:  $m (exit $LASTEXITCODE)" -ForegroundColor Red }
      }
    }
  }
}

if ($InstallService) {
  Write-Host ""
  Write-Host "==== 5. Ollama service ===="

  $ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
  if (-not $ollamaCmd) {
    Write-Host "ERROR: ollama.exe not on PATH — install Ollama first" -ForegroundColor Red
  } else {
    $ollamaExe = $ollamaCmd.Source
    Write-Host "ollama binary: $ollamaExe"

    # The LocalSystem account doesn't share the installing user's %USERPROFILE%,
    # so Ollama defaults (~/.ollama/models) resolve to a different path than
    # where our pulled models live. Pin OLLAMA_MODELS to the installing user's
    # path so we don't have to re-download 20+ GB.
    $userModels = Join-Path $env:USERPROFILE '.ollama\models'
    if (Test-Path $userModels) {
      $currentModels = [Environment]::GetEnvironmentVariable('OLLAMA_MODELS','Machine')
      if ($currentModels -ne $userModels) {
        [Environment]::SetEnvironmentVariable('OLLAMA_MODELS', $userModels, 'Machine')
        Write-Host "set OLLAMA_MODELS=$userModels (was '$currentModels')"
      } else {
        Write-Host "OLLAMA_MODELS already points at user's models dir"
      }
    } else {
      Write-Host "WARN: $userModels not found — service will use LocalSystem's default location"
    }

    # Take over from any currently-running Ollama. Step 4 (model pull)
    # requires a running Ollama to hit /api/tags and `ollama pull`, so by
    # the time we land here port 11434 is almost always held by the tray
    # app (ollama app.exe + its spawned ollama.exe serve). We stop those
    # cleanly — the fresh service will re-open the port within a second.
    $stopped = @()
    foreach ($n in @('ollama','ollama app')) {
      $procs = @(Get-Process -Name $n -ErrorAction SilentlyContinue)
      foreach ($p in $procs) {
        $stopped += "$n(pid=$($p.Id))"
        Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
      }
    }
    if ($stopped.Count -gt 0) {
      Write-Host "stopped existing Ollama instance(s): $($stopped -join ', ')"
      Start-Sleep 2
    }
    # Belt-and-braces: wait briefly for port 11434 to drain. The OS holds
    # the socket in TIME_WAIT after the owner dies, but fresh bind() works
    # once the LISTEN is gone. We're only checking LISTEN state here.
    $deadline = (Get-Date).AddSeconds(10)
    while ((Get-Date) -lt $deadline -and
           ($null -ne (Get-NetTCPConnection -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue))) {
      Start-Sleep -Milliseconds 500
    }
    if ($null -ne (Get-NetTCPConnection -LocalPort 11434 -State Listen -ErrorAction SilentlyContinue)) {
      Write-Host "WARN: port 11434 still held by some other process after 10s — skipping service install" -ForegroundColor Yellow
      Get-NetTCPConnection -LocalPort 11434 -State Listen | Select-Object OwningProcess | Format-Table -AutoSize
    } else {
      # Clean up any failed Windows Service registration from the earlier approach.
      # `ollama.exe serve` is a plain console app and doesn't implement the
      # ServiceCtrlDispatcher protocol, so SCM can't start it natively — that's
      # why New-Service + Start-Service fails with "Cannot start service". We
      # switch to a Task Scheduler task running at system startup as SYSTEM,
      # which achieves the same auto-start + restart-on-failure outcome without
      # needing an external wrapper like NSSM.
      $staleSvc = Get-Service -Name OllamaServer -ErrorAction SilentlyContinue
      if ($staleSvc) {
        if ($staleSvc.Status -ne 'Stopped') {
          Stop-Service -Name OllamaServer -Force -ErrorAction SilentlyContinue
        }
        & sc.exe delete OllamaServer | Out-Null
        Write-Host "removed stale Windows Service 'OllamaServer' (wasn't a viable path — ollama.exe isn't service-aware)"
        Start-Sleep 1
      }

      $taskName = 'OllamaServer'
      $existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
      if ($existingTask) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Host "replaced existing scheduled task '$taskName'"
      }

      $action = New-ScheduledTaskAction `
                  -Execute $ollamaExe `
                  -Argument 'serve' `
                  -WorkingDirectory (Split-Path $ollamaExe -Parent)
      $trigger = New-ScheduledTaskTrigger -AtStartup
      # Hidden + no time limit + multiple-instance ignore so an explicit
      # Start-ScheduledTask doesn't collide with the boot trigger.
      $settings = New-ScheduledTaskSettingsSet `
                    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                    -StartWhenAvailable `
                    -ExecutionTimeLimit ([TimeSpan]::Zero) `
                    -RestartCount 99 -RestartInterval (New-TimeSpan -Minutes 1) `
                    -MultipleInstances IgnoreNew `
                    -Hidden
      $principal = New-ScheduledTaskPrincipal `
                    -UserId 'S-1-5-18' `
                    -LogonType ServiceAccount `
                    -RunLevel Highest
      Register-ScheduledTask -TaskName $taskName `
                             -Description 'Serves Ollama at http://0.0.0.0:11434 for the trading-dashboard backend. Runs as SYSTEM at boot.' `
                             -Action $action -Trigger $trigger `
                             -Settings $settings -Principal $principal | Out-Null
      Write-Host "registered scheduled task '$taskName' (AtStartup, SYSTEM, restart-on-failure every 60s x99)"

      Write-Host "starting task now"
      Start-ScheduledTask -TaskName $taskName

      # Wait up to 15s for Ollama to bind 11434 and answer /api/tags.
      $deadline = (Get-Date).AddSeconds(15)
      $tagsOk = $false
      while ((Get-Date) -lt $deadline) {
        try {
          $tags = Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/tags' -TimeoutSec 3
          Write-Host ("Ollama serving under SYSTEM: {0} model(s) reachable" -f @($tags.models).Count)
          $tagsOk = $true
          break
        } catch {
          Start-Sleep -Milliseconds 500
        }
      }
      if (-not $tagsOk) {
        Write-Host "WARN: task registered but /api/tags didn't answer within 15s" -ForegroundColor Yellow
        Write-Host "      check: Get-ScheduledTaskInfo -TaskName $taskName"
        Write-Host "      and:   Get-Process ollama,'ollama app' -ErrorAction SilentlyContinue"
      }
    }

    # Defensive: tell the user about the tray-app autostart, which would
    # fight our service on next logon. We don't auto-remove it because
    # the Run key lives under the user's hive and touching another user's
    # hive from LocalSystem is messy; a one-line note is enough.
    $runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    $runEntries = @(Get-ItemProperty -Path $runKey -ErrorAction SilentlyContinue | Get-Member -MemberType NoteProperty |
                    Where-Object { $_.Name -like '*llama*' })
    if ($runEntries) {
      Write-Host ""
      Write-Host "NOTE: 'ollama app.exe' autostart is still set in $runKey for this user." -ForegroundColor Yellow
      Write-Host "      After next logon the tray app will try to spawn its own server, which will fail to bind 11434"
      Write-Host "      (service holds it). To remove:"
      Write-Host "      Remove-ItemProperty -Path '$runKey' -Name Ollama"
    }
  }
}

Write-Host ""
Write-Host "==== done ===="
Write-Host "Verify with:"
Write-Host "  Get-NetFirewallRule -DisplayName 'FutuOpenD*','Ollama*' | Select DisplayName,Enabled,Direction"
Write-Host "  [Environment]::GetEnvironmentVariable('OLLAMA_KEEP_ALIVE','Machine')"
Write-Host "  curl http://127.0.0.1:11434/api/tags"
if ($InstallService) {
  Write-Host "  Get-ScheduledTask -TaskName OllamaServer | Get-ScheduledTaskInfo"
}
