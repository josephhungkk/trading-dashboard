#!/usr/bin/env bash
# deploy/nuc/sync-to-windows.sh
# One-way mirror from the WSL-side repo to the Windows-side ops surface (NUC).
#
# Reason: deploy/nuc/ contains PowerShell + VBS scripts that Windows Task Scheduler
# invokes by absolute Windows path (e.g., C:\dashboard\deploy\nuc\Launch-IBKRSidecar.vbs).
# The dev edits happen on the Linux side at /home/joseph/dashboard/deploy/nuc/; this
# helper keeps the Windows-mounted copy in step.
#
# Coverage:
#   - deploy/nuc/      — all .ps1/.vbs (wholesale rsync; new files auto-picked-up,
#                        e.g. wol_helper.ps1, install-wol-helper.ps1, configure-
#                        power-plan.ps1, install-ollama.ps1 added in Phase 11a-A2)
#   - sidecar_ibkr/    — Phase 4 IBKR sidecar source (build via build-windows.ps1)
#   - sidecar_futu/    — Phase 6 Futu sidecar source (build via build-windows-futu.ps1)
#   - proto/           — gRPC contracts (consumed by both build scripts above)
#
# Out of scope:
#   - sidecar_alpaca/ + sidecar_schwab/ + sidecar_schwab_refresher/ — run in Docker
#     on the VPS, NOT on Windows; no Windows-side copy needed.
#   - deploy/heavybox/ — targets the heavy AI box (192.168.50.30), not the NUC;
#     copy those scripts to that host separately (SMB/SCP).
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
#    Cutover completed 2026-05-13: WSL source = sidecar_ibkr/, Windows mirror =
#    C:\dashboard\sidecar_ibkr\, and Launch-IBKRSidecar.vbs + Probe-Sidecar.ps1
#    reference C:\dashboard\sidecar_ibkr\dist\... The legacy C:\dashboard\sidecar\
#    directory can be deleted manually on the Windows side once the new path is
#    confirmed green over a maintenance cycle.
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
echo "[sync] sidecar_ibkr -> $(find "$SIDECAR_DST" -type f | wc -l) files"

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
