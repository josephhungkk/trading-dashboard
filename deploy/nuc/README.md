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
