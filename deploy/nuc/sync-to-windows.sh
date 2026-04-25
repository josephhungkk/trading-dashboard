#!/usr/bin/env bash
# deploy/nuc/sync-to-windows.sh
# One-way mirror from the WSL-side repo to the Windows-side ops surface.
#
# Reason: deploy/nuc/ contains PowerShell + VBS scripts that Windows Task Scheduler
# invokes by absolute Windows path (e.g., C:\dashboard\deploy\nuc\Launch-IBKRSidecar.vbs).
# The dev edits happen on the Linux side at /home/joseph/dashboard/deploy/nuc/; this
# helper keeps the Windows-mounted copy in step.
#
# Run from anywhere; uses absolute paths.
# Idempotent: --delete prunes Windows-side files that were deleted in WSL.
#
# Per CLAUDE.md L125 Phase 4+ work item.

set -euo pipefail

SRC="/home/joseph/dashboard/deploy/nuc/"
DST="/mnt/c/dashboard/deploy/nuc/"

if [ ! -d "/mnt/c/dashboard" ]; then
    echo "[sync] /mnt/c/dashboard does not exist — Windows-side dir not present, nothing to sync."
    echo "[sync] If you genuinely want a Windows-side copy, mkdir it first."
    exit 0
fi

mkdir -p "$DST"
rsync -a --delete "$SRC" "$DST"
echo "[sync] $(ls "$DST" | wc -l) files now in $DST"
