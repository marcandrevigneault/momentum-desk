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
    TRAIL = "TRAIL"   # trailing stop — the broker ratchets it; we set the %


@dataclass
class Order:
    symbol: str
    side: OrderSide
    quantity: int
    type: OrderType = OrderType.MKT
    limit_price: float | None = None
    stop_price: float | None = None
    trailing_percent: float | None = None   # for TRAIL orders


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


def trail_order(plan: PositionPlan, trail_pct: float) -> Order:
    """A trailing protective stop — the broker ratchets it up behind price. This
    is the 10% trail the exit-lab found best, managed broker-side so it survives
    even if our loop dies."""
    return Order(symbol=plan.symbol, side=OrderSide.SELL, quantity=plan.shares,
                 type=OrderType.TRAIL, trailing_percent=trail_pct)


def route_plan(broker, plan: PositionPlan, ref_price: float | None = None,
               trail_pct: float | None = None) -> list[OrderResult]:
    """Submit a risk-approved plan: entry, then its protective stop (a trailing
    stop if trail_pct is given, else a fixed stop). A rejected plan never reaches
    the broker; the protective order is only sent once the entry is live."""
    if not plan.ok:
        return [OrderResult(plan.symbol, "rejected", message="; ".join(plan.reasons) or "plan not ok")]
    entry = broker.place_order(entry_order(plan), ref_price=ref_price)
    results = [entry]
    if entry.status in ("filled", "submitted", "dry_run"):
        protective = trail_order(plan, trail_pct) if trail_pct else stop_order(plan)
        results.append(broker.place_order(protective, ref_price=plan.stop))
    return results
