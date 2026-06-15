"""In-memory simulated broker: immediate fills, position + realized-P&L
tracking. Used by tests and the safe demo so the whole order path can run with
no TWS and no risk. Fills at the order's limit/stop price, or a supplied
reference price for market orders.
"""
from __future__ import annotations

from .base import Order, OrderResult, OrderSide, OrderType, Position


class SimBroker:
    name = "sim"

    def __init__(self) -> None:
        self._pos: dict[str, Position] = {}
        self.fills: list[OrderResult] = []
        self.realized_pnl: float = 0.0

    def connect(self) -> None:  # nothing to connect
        pass

    def disconnect(self) -> None:
        pass

    def positions(self) -> list[Position]:
        return [p for p in self._pos.values() if p.quantity != 0]

    def place_order(self, order: Order, ref_price: float | None = None) -> OrderResult:
        if order.quantity <= 0:
            return OrderResult(order.symbol, "rejected", message="non-positive quantity")

        # A protective STOP is a resting order — it must NOT execute on submit,
        # only when price later trips it (not modeled in this immediate-fill
        # sim). Record it as resting; the position is unchanged.
        if order.type is OrderType.STP:
            if not order.stop_price:
                return OrderResult(order.symbol, "rejected", message="stop order needs a stop price")
            res = OrderResult(order.symbol, "submitted", message=f"resting stop @ {order.stop_price}")
            self.fills.append(res)
            return res

        price = order.limit_price or ref_price
        if price is None or price <= 0:
            return OrderResult(order.symbol, "rejected", message="no fillable price")

        realized = self._apply(order, price)
        res = OrderResult(order.symbol, "filled", filled_qty=order.quantity,
                          avg_fill_price=round(price, 4), realized_pnl=round(realized, 2))
        self.fills.append(res)
        return res

    def _apply(self, order: Order, price: float) -> float:
        """Update the position; return realized P&L for the portion that closes."""
        pos = self._pos.get(order.symbol, Position(order.symbol, 0, 0.0))
        signed = order.quantity if order.side is OrderSide.BUY else -order.quantity
        realized = 0.0

        if pos.quantity == 0 or (pos.quantity > 0) == (signed > 0):
            # opening or adding in the same direction → blend the average price
            new_qty = pos.quantity + signed
            if new_qty != 0:
                pos.avg_price = (pos.avg_price * pos.quantity + price * signed) / new_qty
            pos.quantity = new_qty
        else:
            # reducing/closing → realize against the average price
            closing = min(abs(signed), abs(pos.quantity))
            direction = 1 if pos.quantity > 0 else -1
            realized = (price - pos.avg_price) * closing * direction
            pos.quantity += signed
            if pos.quantity == 0:
                pos.avg_price = 0.0
            elif (pos.quantity > 0) != (direction > 0):
                # flipped through zero → remainder opens at this price
                pos.avg_price = price

        self._pos[order.symbol] = pos
        self.realized_pnl += realized
        return realized
