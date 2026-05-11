"""Generate Python gRPC stubs for the backend from proto/broker/v1/broker.proto.

Cross-platform replacement for the inline shell block used in the GitHub
Actions workflows. Works identically on linux-hosted (ubuntu-latest) and
self-hosted Windows runners (NUC15PRO), where the bash heredoc + sed-i
form does not run under PowerShell.

Usage (from the backend/ directory):
    uv run python scripts/generate_proto_stubs.py

Idempotent: re-running overwrites the generated tree.
"""

from __future__ import annotations

import sys
from pathlib import Path

import grpc_tools  # type: ignore[import-untyped]
from grpc_tools import protoc  # type: ignore[import-untyped]


def main() -> int:
    backend_dir = Path(__file__).resolve().parent.parent
    proto_dir = backend_dir.parent / "proto"
    out_dir = backend_dir / "app" / "_generated"

    if not (proto_dir / "broker" / "v1" / "broker.proto").exists():
        print(f"ERROR: proto file not found at {proto_dir}/broker/v1/broker.proto", file=sys.stderr)
        return 1

    for sub in ("", "broker", "broker/v1"):
        pkg_dir = out_dir / sub
        pkg_dir.mkdir(parents=True, exist_ok=True)
        (pkg_dir / "__init__.py").write_text("")

    # The grpc_tools bundle ships google/protobuf/*.proto well-known types at
    # _proto/. Calling protoc.main() directly bypasses the auto-include that
    # `python -m grpc_tools.protoc` performs, so add it explicitly.
    wellknown_proto = Path(grpc_tools.__file__).resolve().parent / "_proto"

    rc = protoc.main(
        [
            "protoc",
            f"--proto_path={proto_dir}",
            f"--proto_path={wellknown_proto}",
            f"--python_out={out_dir}",
            f"--grpc_python_out={out_dir}",
            f"--pyi_out={out_dir}",
            "broker/v1/broker.proto",
        ]
    )
    if rc != 0:
        print(f"ERROR: protoc failed with exit code {rc}", file=sys.stderr)
        return rc

    grpc_module = out_dir / "broker" / "v1" / "broker_pb2_grpc.py"
    if not grpc_module.exists():
        print(f"ERROR: generated grpc module missing at {grpc_module}", file=sys.stderr)
        return 1
    text = grpc_module.read_text()
    text = text.replace(
        "from broker.v1 import broker_pb2",
        "from app._generated.broker.v1 import broker_pb2",
    )
    grpc_module.write_text(text)

    print(f"[ok] proto codegen complete -> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
