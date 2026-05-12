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

# 2. sidecar_ibkr/ — Phase 4 IBKR sidecar (PyInstaller build, golden-trace recorder).
#    NOTE (2026-05-12): the WSL source dir was renamed sidecar/ -> sidecar_ibkr/ on
#    2026-05-04, but the Windows-side launchers (Launch-IBKRSidecar.vbs:38,
#    Probe-Sidecar.ps1) still reference C:\dashboard\sidecar\dist\... — meaning
#    production sidecars currently run from the OLD path. The launcher-path
#    cutover is a separate operator runbook (see memory windows_sidecar_path_drift).
#    Until then, this sync pushes WSL source to C:\dashboard\sidecar_ibkr\ but
#    the prod .exe is still served from C:\dashboard\sidecar\dist\. After cutover,
#    delete the orphan C:\dashboard\sidecar\ directory.
#    Exclude Linux-built artifacts so we don't push WSL binaries/caches to a Windows path.
SIDECAR_SRC="/home/joseph/dashboard/sidecar_ibkr/"
SIDECAR_DST="/mnt/c/dashboard/sidecar_ibkr/"
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

# 3. proto/ - gRPC contract source consumed by sidecar_ibkr/scripts/build-windows.ps1
#    (uv run python -m grpc_tools.protoc --proto_path=../proto ...). The build
#    script runs from C:\dashboard\sidecar_ibkr\ (post-cutover) or C:\dashboard\sidecar\
#    (pre-cutover); either way it expects ../proto = C:\dashboard\proto.
PROTO_SRC="/home/joseph/dashboard/proto/"
PROTO_DST="/mnt/c/dashboard/proto/"
mkdir -p "$PROTO_DST"
rsync -a --delete "$PROTO_SRC" "$PROTO_DST"
echo "[sync] proto -> $(find "$PROTO_DST" -type f | wc -l) files"

# 4. sidecar_futu/ — Phase 6 Futu sidecar. Same exclusions as sidecar_ibkr/ block above.
#    deploy/nuc/build-windows-futu.ps1 runs from C:\dashboard\sidecar_futu\.
SIDECAR_FUTU_SRC="/home/joseph/dashboard/sidecar_futu/"
SIDECAR_FUTU_DST="/mnt/c/dashboard/sidecar_futu/"
mkdir -p "$SIDECAR_FUTU_DST"
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
    "$SIDECAR_FUTU_SRC" "$SIDECAR_FUTU_DST"
echo "[sync] sidecar_futu -> $(find "$SIDECAR_FUTU_DST" -type f | wc -l) files"
