#!/usr/bin/env bash
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
OUT="sidecar_schwab/_generated"
mkdir -p "$OUT/broker/v1"
uv run --directory sidecar_schwab python -m grpc_tools.protoc \
  -Iproto --python_out="$OUT" --grpc_python_out="$OUT" --pyi_out="$OUT" \
  proto/broker/v1/broker.proto
touch "$OUT/__init__.py" "$OUT/broker/__init__.py" "$OUT/broker/v1/__init__.py"
echo "OK — generated Schwab sidecar stubs in $OUT"
