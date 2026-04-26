# RUNBOOK — GitHub Actions self-hosted runner on the NUC

The Phase 4 nightly-real-IBKR cron (`.github/workflows/nightly-real-ibkr.yml`)
needs a self-hosted runner labeled `self-hosted, nuc` so the job can talk to
the paper Gateway on `127.0.0.1:4002`. This is a one-time NUC setup; not
blocking for tagging v0.4.0.

## Prerequisites

- IBKR paper Gateway already installed and auto-starting via the existing
  `register-ibkr-paper.ps1` Scheduled Task. Confirm with
  `Test-NetConnection 127.0.0.1 -Port 4002` returning `True`.
- Repo admin in GitHub (needed to mint the runner registration token).
- Windows PowerShell 5.1 (default on the NUC). `pwsh` 7+ is fine too.

## One-time setup

1. **Mint the registration token.** On any machine logged into GitHub:

   ```bash
   gh api -X POST repos/<owner>/<repo>/actions/runners/registration-token --jq .token
   ```

   (Or via the GitHub UI: Settings -> Actions -> Runners -> New self-hosted
   runner -> copy the token from the displayed `./config.cmd --token <...>`
   command.)

2. **Install the runner on the NUC** in an elevated PowerShell:

   ```powershell
   $RunnerDir = 'C:\actions-runner'
   New-Item -ItemType Directory -Force -Path $RunnerDir | Out-Null
   Set-Location $RunnerDir

   $ver = (Invoke-RestMethod https://api.github.com/repos/actions/runner/releases/latest).tag_name.TrimStart('v')
   Invoke-WebRequest "https://github.com/actions/runner/releases/download/v$ver/actions-runner-win-x64-$ver.zip" -OutFile runner.zip
   Add-Type -AssemblyName System.IO.Compression.FileSystem
   [IO.Compression.ZipFile]::ExtractToDirectory("$RunnerDir\runner.zip", $RunnerDir)
   Remove-Item runner.zip

   .\config.cmd `
       --url https://github.com/<owner>/<repo> `
       --token <REGISTRATION_TOKEN> `
       --name nuc-runner `
       --labels self-hosted,nuc `
       --runasservice `
       --windowslogonaccount "NT AUTHORITY\SYSTEM"
   ```

   Notes:
   - `--runasservice` registers `actions.runner.<owner>-<repo>.nuc-runner` so
     it starts at boot, not just at user login.
   - LocalSystem may not reach `127.0.0.1:4002` if the Gateway only listens
     on a specific user's loopback. Verify with `psexec -s -i powershell`
     then `Test-NetConnection 127.0.0.1 -Port 4002`. If that fails, switch
     `--windowslogonaccount` to the gateway-owner account and supply its
     password via `--windowslogonpassword`.

3. **Verify the runner is online:**

   ```powershell
   Get-Service "actions.runner.*nuc-runner"
   ```

   Should show `Status: Running`. The GitHub Actions Runners settings page
   should also list `nuc-runner` with a green dot and labels `self-hosted, nuc`.

## Smoke run

Trigger a manual run from the Actions UI: Actions -> "Nightly real-IBKR
contract tests" -> Run workflow. Job should pick up on the NUC, install uv,
generate protos, run sidecar tests against paper Gateway 4002, and finish
green within the 15-minute timeout.

If the run sits queued, check:

- The runner service is running (`Get-Service`).
- Runner labels include BOTH `self-hosted` AND `nuc` — the workflow uses
  `runs-on: [self-hosted, nuc]` which is AND, not OR.
- Repo's runner page shows the runner as "Idle" (not "Offline").

## Teardown

If the runner needs to be retired:

```powershell
# elevated PS in C:\actions-runner
.\config.cmd remove --token <REMOVAL_TOKEN>
Remove-Item C:\actions-runner -Recurse -Force
```

Mint the removal token via:

```bash
gh api -X POST repos/<owner>/<repo>/actions/runners/remove-token --jq .token
```

## Security notes

- Self-hosted runners on a private repo are fine. **Never** label one for use
  with a public repo — fork PRs would run arbitrary code on the NUC.
- The runner's working directory (`C:\actions-runner\_work`) holds the
  checked-out repo and any artifacts. It is owned by the runner account
  (SYSTEM by default). Don't store secrets there; use repo/org-level
  encrypted secrets and `${{ secrets.* }}`.
- Restrict the runner's outbound traffic with the existing Windows firewall
  rules already provisioned by `harden-post-install.ps1`.
