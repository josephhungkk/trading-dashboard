"""C3 — futu OrderStatus -> proto OrderStatus mapping (per spec section 5)."""

from __future__ import annotations

import pytest

from sidecar_futu._generated.broker.v1 import broker_pb2
from sidecar_futu.normalize import status_from_futu_status


@pytest.mark.parametrize(
    ("futu_status", "expected"),
    [
        # spec sec 5 baseline
        ("UNSUBMITTED", broker_pb2.OrderStatus.PENDING),
        ("SUBMITTING", broker_pb2.OrderStatus.PENDING),
        ("WAITING_SUBMIT", broker_pb2.OrderStatus.SUBMITTED),
        ("SUBMITTED", broker_pb2.OrderStatus.SUBMITTED),
        ("FILLED_PART", broker_pb2.OrderStatus.PARTIAL),
        ("FILLED_ALL", broker_pb2.OrderStatus.FILLED),
        ("CANCELLED_PART", broker_pb2.OrderStatus.CANCELLED),
        ("CANCELLED_ALL", broker_pb2.OrderStatus.CANCELLED),
        ("FAILED", broker_pb2.OrderStatus.REJECTED),
        ("DISABLED", broker_pb2.OrderStatus.REJECTED),
        # SDK 10.04 adds five more states the plan did not enumerate
        ("CANCELLING_ALL", broker_pb2.OrderStatus.PENDING),
        ("CANCELLING_PART", broker_pb2.OrderStatus.PARTIAL),
        ("SUBMIT_FAILED", broker_pb2.OrderStatus.REJECTED),
        ("FILL_CANCELLED", broker_pb2.OrderStatus.CANCELLED),
        ("TIMEOUT", broker_pb2.OrderStatus.REJECTED),
    ],
)
def test_status_mapping(futu_status: str, expected: int) -> None:
    assert status_from_futu_status(futu_status) == expected


def test_unknown_status_maps_to_unspecified() -> None:
    assert status_from_futu_status("MOON_PHASE") == broker_pb2.OrderStatus.STATUS_UNSPECIFIED


def test_deleted_maps_to_unspecified_for_backend_translation() -> None:
    """spec sec 5: DELETED is rare and means 'expired by exchange'; backend translates."""
    assert status_from_futu_status("DELETED") == broker_pb2.OrderStatus.STATUS_UNSPECIFIED
