#!/usr/bin/env bash
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
OUT="sidecar_schwab/_generated"
mkdir -p "$OUT/broker/v1"
# Phase 9.6: `uv run --directory sidecar_schwab` switches CWD to
# sidecar_schwab/ before invoking protoc, so the proto include path and
# input file must resolve relative to that subdir. Use absolute paths so
# the script works regardless of how uv interprets CWD.
uv run --directory sidecar_schwab python -m grpc_tools.protoc \
  -I"$ROOT/proto" \
  --python_out="$ROOT/$OUT" \
  --grpc_python_out="$ROOT/$OUT" \
  --pyi_out="$ROOT/$OUT" \
  "$ROOT/proto/broker/v1/broker.proto"
touch "$OUT/__init__.py" "$OUT/broker/__init__.py" "$OUT/broker/v1/__init__.py"
echo "OK — generated Schwab sidecar stubs in $OUT"
