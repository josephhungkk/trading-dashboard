#!/usr/bin/env bash
# backend/scripts/proto-gen.sh
# Regenerate Python gRPC stubs for the backend from proto/broker/v1.proto.
# Run after editing the proto contract.

set -euo pipefail
cd "$(dirname "$0")/.."           # backend/
cd ../proto && buf generate
cd ../backend
mkdir -p app/brokers/_generated
touch app/brokers/_generated/__init__.py
echo "[ok] backend proto codegen complete -> app/brokers/_generated/"
