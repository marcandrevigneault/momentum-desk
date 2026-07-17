"""The order/position types the desk speaks.

`SimBroker` consumes these for the paper-practice desk; the real path
(live_transmit → broker/cp) builds CP API payloads directly and shares only the
vocabulary. The risk engine sizes; nothing here ever invents a size of its own.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    MKT = "MKT"
    LMT = "LMT"
    STP = "STP"


@dataclass
class Order:
    symbol: str
    side: OrderSide
    quantity: int
    type: OrderType = OrderType.MKT
    limit_price: float | None = None
    stop_price: float | None = None


@dataclass
class OrderResult:
    symbol: str
    status: str            # "filled" | "submitted" | "dry_run" | "rejected"
    filled_qty: int = 0
    avg_fill_price: float = 0.0
    realized_pnl: float = 0.0
    message: str = ""

    @property
    def is_fill(self) -> bool:
        return self.status == "filled"


@dataclass
class Position:
    symbol: str
    quantity: int
    avg_price: float
