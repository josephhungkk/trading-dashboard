"""Tests for Phase 15b OrderBook / book_manager."""

from __future__ import annotations

from decimal import Decimal

from app.services.crypto.book_manager import MAX_BOOK_DEPTH, OrderBook


def test_apply_delta_adds_bid() -> None:
    book = OrderBook()
    book.apply_delta("bid", Decimal("50000"), Decimal("0.5"), 1)
    assert book.bids[Decimal("50000")] == Decimal("0.5")


def test_apply_delta_removes_on_zero_qty() -> None:
    book = OrderBook(bids={Decimal("50000"): Decimal("0.5")})
    book.apply_delta("bid", Decimal("50000"), Decimal("0"), 2)
    assert Decimal("50000") not in book.bids


def test_apply_delta_bounds_bids_to_max_depth() -> None:
    book = OrderBook()
    for i in range(MAX_BOOK_DEPTH + 5):
        book.apply_delta("bid", Decimal(str(50000 + i)), Decimal("0.1"), i)
    assert len(book.bids) == MAX_BOOK_DEPTH


def test_apply_delta_keeps_top_bids() -> None:
    book = OrderBook()
    for i in range(MAX_BOOK_DEPTH + 5):
        book.apply_delta("bid", Decimal(str(i)), Decimal("0.1"), i)
    assert min(book.bids.keys()) >= Decimal("5")


def test_apply_delta_bounds_asks_to_max_depth() -> None:
    book = OrderBook()
    for i in range(MAX_BOOK_DEPTH + 5):
        book.apply_delta("ask", Decimal(str(50000 + i)), Decimal("0.1"), i)
    assert len(book.asks) == MAX_BOOK_DEPTH


def test_apply_delta_keeps_lowest_asks() -> None:
    book = OrderBook()
    for i in range(MAX_BOOK_DEPTH + 5):
        book.apply_delta("ask", Decimal(str(i)), Decimal("0.1"), i)
    assert max(book.asks.keys()) <= Decimal(str(MAX_BOOK_DEPTH - 1))


def test_snapshot_returns_depth_levels() -> None:
    book = OrderBook(bids={Decimal("50001"): Decimal("1"), Decimal("50000"): Decimal("2")})
    snap = book.snapshot(depth=1)
    assert len(snap["bids"]) == 1
    assert snap["bids"][0][0] == Decimal("50001")


def test_last_seq_updated() -> None:
    book = OrderBook()
    book.apply_delta("ask", Decimal("50001"), Decimal("0.3"), 42)
    assert book.last_seq == 42
