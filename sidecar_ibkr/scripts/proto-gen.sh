#!/usr/bin/env bash
# sidecar_ibkr/scripts/proto-gen.sh
# Regenerate Python gRPC stubs for the sidecar from proto/broker/v1/broker.proto.
#
# Prefers `buf` (the spec-blessed plugin chain) but falls back to a local
# grpc_tools.protoc invocation + import rewrite for dev environments where
# buf is unavailable. The fallback produces byte-equivalent module surface
# for `from sidecar_ibkr._generated.broker.v1 import broker_pb2` callers.

set -euo pipefail
cd "$(dirname "$0")/.."           # sidecar_ibkr/

mkdir -p _generated/broker/v1
: > _generated/__init__.py
: > _generated/broker/__init__.py
: > _generated/broker/v1/__init__.py

if command -v buf >/dev/null 2>&1; then
  ( cd ../proto && buf generate )
  # `buf generate` writes to BOTH backend/app/_generated/ and sidecar_ibkr/_generated/
  # per proto/buf.gen.yaml. Ensure package __init__.py files exist on both
  # sides and rewrite the broken `from v1 import broker_pb2` import.
  mkdir -p ../backend/app/_generated/broker/v1
  : > ../backend/app/_generated/__init__.py
  : > ../backend/app/_generated/broker/__init__.py
  : > ../backend/app/_generated/broker/v1/__init__.py
  sed -i 's|^from v1 import broker_pb2|from app._generated.broker.v1 import broker_pb2|' \
    ../backend/app/_generated/broker/v1/broker_pb2_grpc.py
  sed -i 's|^from v1 import broker_pb2|from sidecar_ibkr._generated.broker.v1 import broker_pb2|' \
    _generated/broker/v1/broker_pb2_grpc.py
  echo "[ok] proto codegen complete -> backend/app/_generated/broker/v1/ + sidecar_ibkr/_generated/broker/v1/"
  exit 0
fi

echo "[warn] buf not installed; falling back to grpc_tools.protoc"
uv run python -m grpc_tools.protoc \
  --proto_path=../proto \
  --python_out=_generated \
  --grpc_python_out=_generated \
  --pyi_out=_generated \
  broker/v1/broker.proto

# grpc_tools generates `from broker.v1 import broker_pb2` which breaks under
# the sidecar_ibkr._generated.broker.v1 package layout. Rewrite to a fully-
# qualified import so the file works wherever the package is imported.
sed -i 's|^from broker\.v1 import broker_pb2|from sidecar_ibkr._generated.broker.v1 import broker_pb2|' \
  _generated/broker/v1/broker_pb2_grpc.py

echo "[ok] sidecar proto codegen complete via grpc_tools -> sidecar_ibkr/_generated/"
