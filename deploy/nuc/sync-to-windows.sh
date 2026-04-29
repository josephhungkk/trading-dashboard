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
# Per CLAUDE.md "Phase 4+ work item" in Project Paths section.

set -euo pipefail

if [ ! -d "/mnt/c/dashboard" ]; then
    echo "[sync] /mnt/c/dashboard does not exist — Windows-side dir not present, nothing to sync."
    echo "[sync] If you genuinely want a Windows-side copy, mkdir it first."
    exit 0
fi

# 1. deploy/nuc/ — Phase 1 broker ops glue (Scheduled Tasks invoke .ps1/.vbs by absolute path).
DEPLOY_SRC="/home/joseph/dashboard/deploy/nuc/"
DEPLOY_DST="/mnt/c/dashboard/deploy/nuc/"
mkdir -p "$DEPLOY_DST"
rsync -a --delete "$DEPLOY_SRC" "$DEPLOY_DST"
echo "[sync] deploy/nuc -> $(find "$DEPLOY_DST" -type f | wc -l) files"

# 2. sidecar/ — Phase 4 IBKR sidecar (PyInstaller build, golden-trace recorder, Scheduled
#    Task launchers all run from C:\dashboard\sidecar\). Exclude Linux-built artifacts so
#    we don't push WSL binaries/caches to a Windows path.
SIDECAR_SRC="/home/joseph/dashboard/sidecar/"
SIDECAR_DST="/mnt/c/dashboard/sidecar/"
mkdir -p "$SIDECAR_DST"
rsync -a --delete \
    --exclude '_generated/' \
    --exclude '__pycache__/' \
    --exclude '.pytest_cache/' \
    --exclude '.mypy_cache/' \
    --exclude '.ruff_cache/' \
    --exclude '.venv/' \
    --exclude 'build/' \
    --exclude 'dist/' \
    --exclude '*.egg-info/' \
    "$SIDECAR_SRC" "$SIDECAR_DST"
echo "[sync] sidecar -> $(find "$SIDECAR_DST" -type f | wc -l) files"

# 3. proto/ - gRPC contract source consumed by sidecar/scripts/build-windows.ps1
#    (uv run python -m grpc_tools.protoc --proto_path=../proto ...). The build
#    script runs from C:\dashboard\sidecar\, so it expects ../proto = C:\dashboard\proto.
PROTO_SRC="/home/joseph/dashboard/proto/"
PROTO_DST="/mnt/c/dashboard/proto/"
mkdir -p "$PROTO_DST"
rsync -a --delete "$PROTO_SRC" "$PROTO_DST"
echo "[sync] proto -> $(find "$PROTO_DST" -type f | wc -l) files"
