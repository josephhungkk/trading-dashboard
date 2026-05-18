from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

MAX_BOOK_DEPTH = 100


@dataclass
class OrderBook:
    bids: dict[Decimal, Decimal] = field(default_factory=dict)
    asks: dict[Decimal, Decimal] = field(default_factory=dict)
    last_seq: int = 0

    def apply_delta(self, side: str, price: Decimal, qty: Decimal, seq: int) -> None:
        book = self.bids if side == "bid" else self.asks
        if qty == Decimal("0"):
            book.pop(price, None)
        else:
            book[price] = qty
        if side == "bid" and len(book) > MAX_BOOK_DEPTH:
            to_evict = sorted(book)[: len(book) - MAX_BOOK_DEPTH]
            for p in to_evict:
                del book[p]
        elif side == "ask" and len(book) > MAX_BOOK_DEPTH:
            to_evict = sorted(book, reverse=True)[: len(book) - MAX_BOOK_DEPTH]
            for p in to_evict:
                del book[p]
        self.last_seq = seq

    def snapshot(self, depth: int = 20) -> dict[str, list[tuple[Decimal, Decimal]]]:
        bids = sorted(self.bids.items(), reverse=True)[:depth]
        asks = sorted(self.asks.items())[:depth]
        return {"bids": list(bids), "asks": list(asks)}
