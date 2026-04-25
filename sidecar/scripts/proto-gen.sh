#!/usr/bin/env bash
# sidecar/scripts/proto-gen.sh
# Regenerate Python gRPC stubs for the sidecar from proto/broker/v1.proto.

set -euo pipefail
cd "$(dirname "$0")/.."           # sidecar/
cd ../proto && buf generate
cd ../sidecar
mkdir -p _generated
touch _generated/__init__.py
echo "[ok] sidecar proto codegen complete -> sidecar/_generated/"
