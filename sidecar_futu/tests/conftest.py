"""Pytest configuration for sidecar_futu tests.

The generated grpc stub ``broker_pb2_grpc.py`` uses an absolute import::

    from broker.v1 import broker_pb2 as ...

This requires ``sidecar_futu/_generated`` to be on ``sys.path``.  We add it
here so all test modules that import ``handlers`` work without modification.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow ``from broker.v1 import broker_pb2`` used in the generated grpc stub.
_generated = Path(__file__).parent.parent / "_generated"
if str(_generated) not in sys.path:
    sys.path.insert(0, str(_generated))
