# deploy/nuc — Windows ops glue placeholder

This directory will hold PowerShell + VBS helpers for the NUC15PRO:

- Broker auto-start (IB Gateway × 4 accounts, FutuOpenD)
- TOTP fill + 2FA handling
- Window hider (hides broker GUIs from the desktop)
- Watchdog (every 5 min; restarts dead brokers)
- Tray app showing broker health
- Daily restart scheduler

Phase 0 leaves this empty. The legacy deployment has working versions at
`C:\dashboard\deploy\nuc\*` on the live tree — those stay untouched
during Phase 0–1. Rewrite or port into this repo during Phase 4+
as each broker lands.

See memory:
- `ps1_nuc_bom_crlf.md` — PS1 files must be UTF-8 BOM + CRLF.
- `feedback_ibc_gotchas.md` — IBC multi-account quirks.
- `powershell_whereobject_unroll.md` — `Where-Object` `.Count` trap.

## Phase 5b — pre-deploy smoke

Before deploying any build that touches the broker sidecar or trade-execution paths, run the real-IBKR smoke suite against a paper gateway to verify the live gRPC contract.

### When to run
- After any change to sidecar/app/ proto definitions, order placement, or order-event streaming (Phase 5b tasks B1–B5).
- Before tagging a release that includes Phase 5b code.
- After rotating mTLS certificates on the sidecar.

### How to run
1. Open GitHub Actions → Pre-deploy smoke (real IBKR paper) → Run workflow.
2. Choose the gateway label: isa-paper (ISA paper account) or normal-paper (normal paper account).
3. The workflow runs on the self-hosted runner labeled nuc (the NUC15PRO). Ensure the runner is online and IB Gateway is running in paper mode on the corresponding port (18002 for isa-paper, 18004 for normal-paper).
4. Review the pytest output. All tests in tests/test_real_ibkr_smoke.py must pass.
5. If any test fails, do NOT proceed with the deploy. Investigate the failure and re-run after fixing.

### Permissions
Only repository members with Actions write access (josephhungkk@gmail.com and ispyling@gmail.com) can trigger workflow_dispatch. CI service-token callers cannot trigger manual workflows.

### Environment variables set by the workflow
- REAL_IBKR=1 — enables the @pytest.mark.skipif gate in the smoke tests.
- IBKR_PAPER_GATEWAY — the gateway label passed to the sidecar fixture.
