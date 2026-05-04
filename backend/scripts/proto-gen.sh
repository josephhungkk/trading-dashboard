#!/usr/bin/env bash
# backend/scripts/proto-gen.sh
# Regenerate Python gRPC stubs for the backend from proto/broker/v1/broker.proto.
# Run after editing the proto contract.

set -euo pipefail
cd "$(dirname "$0")/.."           # backend/
cd ../proto && buf generate
cd ..
# `buf generate` writes to BOTH backend/app/_generated/ and sidecar_ibkr/_generated/
# per proto/buf.gen.yaml. Ensure package __init__.py files exist on both sides
# and rewrite the broken `from v1 import broker_pb2` import to a fully-
# qualified import so the stubs are loadable.
mkdir -p backend/app/_generated/broker/v1 sidecar_ibkr/_generated/broker/v1
: > backend/app/_generated/__init__.py
: > backend/app/_generated/broker/__init__.py
: > backend/app/_generated/broker/v1/__init__.py
: > sidecar_ibkr/_generated/__init__.py
: > sidecar_ibkr/_generated/broker/__init__.py
: > sidecar_ibkr/_generated/broker/v1/__init__.py
sed -i 's|^from v1 import broker_pb2|from app._generated.broker.v1 import broker_pb2|' \
  backend/app/_generated/broker/v1/broker_pb2_grpc.py
sed -i 's|^from v1 import broker_pb2|from sidecar._generated.broker.v1 import broker_pb2|' \
  sidecar_ibkr/_generated/broker/v1/broker_pb2_grpc.py
echo "[ok] proto codegen complete -> backend/app/_generated/broker/v1/ + sidecar_ibkr/_generated/broker/v1/"
