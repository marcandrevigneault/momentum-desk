"""Broker contract + the order types the desk speaks.

A broker is anything that can take an `Order` and report fills/positions: the
`SimBroker` for tests and safe demos, the `IBKRBroker` for a real (paper)
account. The risk engine sizes; this layer only routes — and never invents a
size of its own.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from ..risk import PositionPlan


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


@runtime_checkable
class BrokerAdapter(Protocol):
    name: str

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def place_order(self, order: Order, ref_price: float | None = None) -> OrderResult: ...
    def positions(self) -> list[Position]: ...


# ---- plan → orders (the risk engine decided the size; we just translate) ----
def entry_order(plan: PositionPlan, order_type: OrderType = OrderType.MKT) -> Order:
    return Order(
        symbol=plan.symbol, side=OrderSide.BUY, quantity=plan.shares,
        type=order_type, limit_price=plan.entry if order_type is OrderType.LMT else None,
    )


def stop_order(plan: PositionPlan) -> Order:
    """The protective stop that must accompany every entry — non-negotiable."""
    return Order(symbol=plan.symbol, side=OrderSide.SELL, quantity=plan.shares,
                 type=OrderType.STP, stop_price=plan.stop)


def route_plan(broker, plan: PositionPlan, ref_price: float | None = None) -> list[OrderResult]:
    """Submit a risk-approved plan: entry, then its protective stop. A rejected
    plan never reaches the broker. A stop is only sent once the entry is live."""
    if not plan.ok:
        return [OrderResult(plan.symbol, "rejected", message="; ".join(plan.reasons) or "plan not ok")]
    entry = broker.place_order(entry_order(plan), ref_price=ref_price)
    results = [entry]
    if entry.status in ("filled", "submitted", "dry_run"):
        results.append(broker.place_order(stop_order(plan), ref_price=plan.stop))
    return results
