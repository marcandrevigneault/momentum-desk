"""Interactive Brokers adapter (paper-first).

Three independent safety layers, because this is the one module that can move
real money:

  1. **Paper ports only by default.** 7497 (TWS paper) / 4002 (Gateway paper)
     are allowed; the live ports 7496 / 4001 are refused unless you pass
     ``allow_live=True`` *explicitly*.
  2. **Dry-run by default.** ``place_order`` logs the intended order and returns
     status ``dry_run`` without transmitting. You must construct with
     ``dry_run=False`` to actually send.
  3. **Lazy import.** ``ib_async`` is only imported in ``connect()``, so the
     package (and tests) work without the broker dependency installed.

`ib_async` talks to a running TWS / IB Gateway — login and any 2FA happen
there, not here.
"""
from __future__ import annotations

from .base import Order, OrderResult, OrderType, Position

PAPER_PORTS = (7497, 4002)
LIVE_PORTS = (7496, 4001)


class IBKRBroker:
    name = "ibkr"

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 7497,
        client_id: int = 17,
        dry_run: bool = True,
        allow_live: bool = False,
    ) -> None:
        self.is_paper_port = port in PAPER_PORTS
        if not self.is_paper_port and not allow_live:
            raise ValueError(
                f"port {port} is not a known paper port {PAPER_PORTS}; "
                "refusing to connect to a live account without allow_live=True"
            )
        self.host, self.port, self.client_id = host, port, client_id
        self.dry_run = dry_run
        self.allow_live = allow_live
        self._ib = None  # set on connect()

    def connect(self) -> None:
        try:
            from ib_async import IB  # lazy: only needed for a real connection
        except ImportError as e:  # noqa: TRY003
            raise RuntimeError(
                "ib_async is not installed — `pip install momentum-desk[broker]` "
                "(or `pip install ib-async`) to connect to IBKR"
            ) from e
        ib = IB()
        ib.connect(self.host, self.port, clientId=self.client_id)
        self._ib = ib

    def disconnect(self) -> None:
        if self._ib is not None:
            self._ib.disconnect()
            self._ib = None

    def positions(self) -> list[Position]:
        if self._ib is None:
            return []
        out = []
        for p in self._ib.positions():
            out.append(Position(symbol=p.contract.symbol, quantity=int(p.position),
                                avg_price=float(p.avgCost)))
        return out

    def place_order(self, order: Order, ref_price: float | None = None) -> OrderResult:
        # Layer 2: dry-run short-circuits before any transmission.
        if self.dry_run:
            return OrderResult(
                order.symbol, "dry_run", filled_qty=0,
                message=f"DRY RUN — would {order.side} {order.quantity} {order.symbol} "
                        f"({order.type}{f' @ {order.limit_price}' if order.limit_price else ''})",
            )
        if self._ib is None:
            return OrderResult(order.symbol, "rejected", message="not connected")

        from ib_async import LimitOrder, MarketOrder, Stock, StopOrder
        contract = Stock(order.symbol, "SMART", "USD")
        if order.type is OrderType.LMT:
            ib_order = LimitOrder(order.side.value, order.quantity, order.limit_price)
        elif order.type is OrderType.STP:
            ib_order = StopOrder(order.side.value, order.quantity, order.stop_price)
        else:
            ib_order = MarketOrder(order.side.value, order.quantity)

        trade = self._ib.placeOrder(contract, ib_order)
        return OrderResult(
            order.symbol, "submitted", message=f"order id {getattr(trade.order, 'orderId', '?')}"
        )
