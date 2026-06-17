"""IBKR Client Portal broker adapter (paper-first) — the bravos-style flow.

Wraps the async ``cp.IBKRClient`` behind the synchronous ``BrokerAdapter``
contract the desk speaks, so ``route_plan`` / ``paper.py`` can drive it
unchanged. Connection + 2FA happen at the gateway (ibeam auto-login + one phone
tap); this adapter only routes risk-approved orders.

Three safety layers, same posture as the socket adapter in ``ibkr.py``:
  1. **Dry-run by default** — ``place_order`` returns ``dry_run`` without
     transmitting unless constructed with ``dry_run=False``.
  2. **Paper-first** — ``require_paper=True`` (default) refuses to route once the
     gateway reports it is authenticated into a *live* account, unless
     ``allow_live=True`` is passed explicitly.
  3. **Lazy + isolated** — the async client and its event loop are created on
     ``connect()``; nothing happens at import time.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from .base import Order, OrderResult, OrderSide, OrderType, Position
from .cp import (
    AccountSummary,
    GatewayHealth,
    IBKRClient,
    IBKRError,
    IBKROrderHalted,
)
from .cp import check as gateway_check
from .cp import orders as order_builders

log = logging.getLogger("momentum_desk.broker.cp")


class IBKRCPBroker:
    name = "ibkr_cp"

    def __init__(
        self,
        gateway_url: str = "https://localhost:5000/v1/api",
        account_id: str = "",
        *,
        dry_run: bool = True,
        require_paper: bool = True,
        allow_live: bool = False,
    ) -> None:
        self.gateway_url = gateway_url
        self.account_id = account_id
        self.dry_run = dry_run
        self.require_paper = require_paper
        self.allow_live = allow_live
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: IBKRClient | None = None
        self._last_tickle_at: datetime | None = None

    # ---- lifecycle (sync façade over the async client) --------------------

    def _run(self, coro):
        if self._loop is None or self._client is None:
            raise RuntimeError("not connected — call connect() first")
        return self._loop.run_until_complete(coro)

    def connect(self) -> None:
        """Open the client and verify the gateway is authenticated. Does NOT log
        in — that's ibeam + your phone tap at the gateway."""
        self._loop = asyncio.new_event_loop()
        self._client = IBKRClient(self.gateway_url, account_id=self.account_id)
        health = self._run(gateway_check(self._client))
        if not health.ok:
            raise IBKRError(
                f"gateway not ready (authenticated={health.authenticated} "
                f"connected={health.connected} competing={health.competing}); "
                "complete the IBKR login + phone 2FA at the gateway first"
            )
        self.account_id = self._run(self._client.resolve_account_id())
        # Paper accounts are prefixed 'DU' (demo user); live are 'U'. Guard live.
        if self.require_paper and not self.allow_live and not self.account_id.upper().startswith("DU"):
            raise IBKRError(
                f"gateway is authenticated into a non-paper account ({self.account_id}); "
                "refusing to route without allow_live=True"
            )

    def disconnect(self) -> None:
        if self._client is not None and self._loop is not None:
            try:
                self._loop.run_until_complete(self._client.aclose())
            finally:
                self._loop.close()
        self._client = None
        self._loop = None

    # ---- health (for the dashboard) ---------------------------------------

    def health(self) -> GatewayHealth:
        return self._run(gateway_check(self._client, last_tickle_at=self._last_tickle_at))

    def summary(self) -> AccountSummary:
        return self._run(self._client.get_summary(self.account_id))

    def positions(self) -> list[Position]:
        if self._client is None:
            return []
        try:
            return self._run(self._client.get_positions(self.account_id))
        except IBKRError as e:
            log.warning("positions failed: %s", e)
            return []

    # ---- order routing -----------------------------------------------------

    def place_order(self, order: Order, ref_price: float | None = None) -> OrderResult:
        # Layer 1: dry-run short-circuits before any transmission.
        if self.dry_run:
            return OrderResult(
                order.symbol, "dry_run", filled_qty=0,
                message=f"DRY RUN — would {order.side} {order.quantity} {order.symbol} "
                        f"({order.type}{f' @ {order.limit_price}' if order.limit_price else ''})",
            )
        if self._client is None:
            return OrderResult(order.symbol, "rejected", message="not connected")
        try:
            return self._run(self._place_async(order))
        except IBKROrderHalted as e:
            return OrderResult(order.symbol, "rejected", message=f"halted: {e.reason}")
        except IBKRError as e:
            return OrderResult(order.symbol, "rejected", message=str(e))

    async def _place_async(self, order: Order) -> OrderResult:
        assert self._client is not None
        conid = await self._client.resolve_conid(order.symbol)
        if conid is None:
            return OrderResult(order.symbol, "rejected", message="could not resolve conid")
        side = "BUY" if order.side is OrderSide.BUY else "SELL"
        if order.type is OrderType.LMT:
            payload = order_builders.build_limit_order(
                self.account_id, conid, side, order.quantity, order.limit_price
            )
        elif order.type is OrderType.STP:
            payload = order_builders.build_stop_order(
                self.account_id, conid, side, order.quantity, order.stop_price
            )
        else:
            payload = order_builders.build_market_order(self.account_id, conid, side, order.quantity)
        resp = await self._client.place_order_with_replies(self.account_id, payload)
        oid = IBKRClient.extract_order_id(resp)
        return OrderResult(order.symbol, "submitted", message=f"order id {oid}" if oid else "submitted")
