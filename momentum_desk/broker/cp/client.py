"""Async HTTP client for the IBKR Client Portal Web API.

Adapted from bravos-interactive-link. All methods use a single injected
``httpx.AsyncClient`` so tests can swap in ``httpx.MockTransport``. The CP API
authenticates via the browser/ibeam login at the gateway — none of these
endpoints ever see the IBKR password directly.

Endpoints (ref: https://www.interactivebrokers.com/campus/ibkr-api-page/cpapi-v1/):
- POST   /tickle                                    keepalive
- GET    /iserver/auth/status                       session health
- GET    /iserver/accounts                          account IDs
- GET    /portfolio/{accountId}/positions/{page}    positions
- GET    /portfolio/{accountId}/summary + /ledger   NAV / cash / PnL
- GET    /iserver/secdef/search                     symbol -> conid
- POST   /iserver/account/{accountId}/orders        place orders
- POST   /iserver/reply/{replyId}                   confirm a warning
- DELETE /iserver/account/{accountId}/order/{id}    cancel
- GET    /iserver/account/orders                    live orders
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from ..base import Position

log = logging.getLogger("momentum_desk.broker.cp")

# Auto-ack allowlist (case-insensitive substring match) — conservative on purpose.
#   "price"               -> IBKR price-cap / percentage-constraint nudge; the
#                            order was built with intent, accepting the cap is safe.
#   "cash quantity"       -> confirms cash-quantity sizing (always intentional).
#   "mandatory cap"       -> MKT-order mandatory price cap for some venues; the
#                            protective stop child contains the risk.
#   "without market data" -> paper accounts ship without live data subs and IBKR
#                            raises this on every order; acking unblocks paper flow.
# Anything else (margin changes, size multipliers, after-hours, etc.) raises
# IBKROrderHalted for manual review.
DEFAULT_AUTO_ACK_MESSAGES: frozenset[str] = frozenset(
    {"price", "cash quantity", "mandatory cap", "without market data"}
)

_MAX_REPLY_LOOPS = 5

# US listing exchanges accepted by resolve_conid when wanting USD. IBKR's secdef
# search returns one row per listing venue; anything outside this set is treated
# as a foreign listing and skipped to avoid routing to an exchange the account
# has no permissions on (a real bravos bug: IYT resolved to its Mexican conid).
_US_EXCHANGES: frozenset[str] = frozenset(
    {"NYSE", "NASDAQ", "ARCA", "AMEX", "BATS", "IEX", "PINK", "OTC", "CBOE", "EDGX"}
)


class IBKRError(Exception):
    """Base class for all IBKR CP client errors."""


class IBKRAuthError(IBKRError):
    """Gateway returned authenticated=false."""


class IBKRCompetingSessionError(IBKRError):
    """Gateway returned competing=true — another session grabbed the auth."""


@dataclass
class IBKROrderHalted(IBKRError):
    """A reply-warning not in the auto-ack allowlist. The caller must halt."""

    reason: str
    reply_id: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"IBKR order halted: {self.reason!r} (reply_id={self.reply_id})"


@dataclass
class AccountSummary:
    """Minimal account-equity snapshot for the dashboard."""

    account_id: str
    nav: Decimal
    cash: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal | None = None


class IBKRClient:
    """Async wrapper around the CP Web API."""

    def __init__(
        self,
        base_url: str,
        client: httpx.AsyncClient | None = None,
        *,
        account_id: str = "",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.account_id = account_id
        self._owns_client = client is None
        if client is None:
            # verify=False is fine: localhost gateway, self-signed cert.
            client = httpx.AsyncClient(
                base_url=self.base_url,
                verify=False,
                timeout=httpx.Timeout(10.0, connect=5.0),
            )
        elif not str(getattr(client, "base_url", "")):
            client.base_url = self.base_url
        self._client = client
        self._conid_cache: dict[str, int] = {}  # in-memory (bravos used SQLite)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ---- low-level helpers ------------------------------------------------

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        resp = await self._client.request(method, path, **kwargs)
        resp.raise_for_status()
        return resp

    async def _ensure_authenticated(self) -> dict[str, Any]:
        status = await self.auth_status()
        if status.get("competing"):
            raise IBKRCompetingSessionError(status.get("message") or "competing session")
        if not status.get("authenticated", False):
            raise IBKRAuthError(status.get("message") or "not authenticated")
        return status

    # ---- session / auth ---------------------------------------------------

    async def auth_status(self) -> dict[str, Any]:
        resp = await self._request("GET", "/iserver/auth/status")
        return resp.json()

    async def tickle(self) -> None:
        await self._request("POST", "/tickle")

    async def list_accounts(self) -> list[str]:
        """Return known account IDs; fall back to the configured account_id."""
        try:
            resp = await self._request("GET", "/iserver/accounts")
            accounts = (resp.json() or {}).get("accounts") or []
            if accounts:
                return [str(a) for a in accounts]
        except (httpx.HTTPError, ValueError) as e:
            log.warning("list_accounts failed: %s", e)
        return [self.account_id] if self.account_id else []

    async def resolve_account_id(self) -> str:
        """The configured account, else the first one the gateway reports."""
        if self.account_id:
            return self.account_id
        accounts = await self.list_accounts()
        if not accounts:
            raise IBKRError("no account id configured and gateway returned none")
        self.account_id = accounts[0]
        return self.account_id

    # ---- portfolio --------------------------------------------------------

    async def get_summary(self, account_id: str) -> AccountSummary:
        """Snapshot account equity. Combines /summary (NAV + cash) with /ledger
        (PnL) — /summary has no unrealizedpnl field, so a single call always
        reports PnL=0. The ledger's BASE row carries it in the base currency."""
        await self._ensure_authenticated()
        resp = await self._request("GET", f"/portfolio/{account_id}/summary")
        data = resp.json()

        def _amt(key: str) -> Decimal:
            node = data.get(key) or {}
            raw = node.get("amount", "0") if isinstance(node, dict) else node
            try:
                return Decimal(str(raw))
            except (InvalidOperation, TypeError):
                return Decimal("0")

        unrealized = Decimal("0")
        realized: Decimal | None = None
        try:
            ledger = (await self._request("GET", f"/portfolio/{account_id}/ledger")).json() or {}
        except (httpx.HTTPError, ValueError) as e:
            log.warning("ledger fetch failed: %s", e)
            ledger = {}
        base = ledger.get("BASE") if isinstance(ledger, dict) else None
        if isinstance(base, dict):
            try:
                unrealized = Decimal(str(base.get("unrealizedpnl") or 0))
            except (InvalidOperation, TypeError):
                unrealized = Decimal("0")
            try:
                realized = Decimal(str(base.get("realizedpnl") or 0))
            except (InvalidOperation, TypeError):
                realized = None
        elif isinstance(ledger, dict):
            for k, row in ledger.items():
                if not isinstance(row, dict) or k == "BASE":
                    continue
                try:
                    unrealized += Decimal(str(row.get("unrealizedpnl") or 0))
                except (InvalidOperation, TypeError):
                    continue

        return AccountSummary(
            account_id=account_id,
            nav=_amt("netliquidation"),
            cash=_amt("availablefunds"),
            unrealized_pnl=unrealized,
            realized_pnl=realized,
        )

    async def get_positions(self, account_id: str) -> list[Position]:
        await self._ensure_authenticated()
        resp = await self._request("GET", f"/portfolio/{account_id}/positions/0")
        out: list[Position] = []
        for row in resp.json() or []:
            try:
                out.append(
                    Position(
                        symbol=str(row.get("contractDesc") or row.get("ticker") or ""),
                        quantity=int(Decimal(str(row.get("position", "0")))),
                        avg_price=float(Decimal(str(row.get("avgCost", "0")))),
                    )
                )
            except (InvalidOperation, TypeError, ValueError) as e:
                log.warning("position parse failed: %s (row=%s)", e, row)
        return out

    # ---- contract resolution ----------------------------------------------

    async def resolve_conid(
        self,
        symbol: str,
        *,
        sec_type: str = "STK",
        currency: str = "USD",
    ) -> int | None:
        """Resolve symbol -> conid. In-memory cache first, then secdef/search.

        Disambiguates foreign listings: secdef/search returns one row per
        listing exchange, and the first match wins if unfiltered. Reject rows
        whose currency differs, require a positive US signal when wanting USD,
        then score (+2 currency match, +1 US exchange) and pick the best.
        """
        if symbol in self._conid_cache:
            return self._conid_cache[symbol]
        try:
            resp = await self._request("GET", "/iserver/secdef/search", params={"symbol": symbol})
        except httpx.HTTPError as e:
            log.warning("secdef search failed for %s: %s", symbol, e)
            return None

        want_sec, want_ccy = sec_type.upper(), currency.upper()
        candidates: list[tuple[int, int]] = []  # (score, conid)
        for row in resp.json() or []:
            row_sec = (row.get("secType") or "").upper()
            row_ccy = (row.get("currency") or "").upper()
            row_exch = (
                row.get("description") or row.get("exchange") or row.get("listingExchange") or ""
            ).upper()
            sections = row.get("sections") or []
            sec_ok = row_sec == want_sec or any(
                (s.get("secType") or "").upper() == want_sec for s in sections
            )
            if not sec_ok:
                continue
            if row_ccy and row_ccy != want_ccy:
                continue
            if want_ccy == "USD" and not (row_ccy == want_ccy or row_exch in _US_EXCHANGES):
                if row_exch:  # has exchange info, just not US — reject
                    continue
            try:
                conid = int(row["conid"])
            except (KeyError, TypeError, ValueError):
                continue
            score = (2 if row_ccy == want_ccy else 0) + (1 if row_exch in _US_EXCHANGES else 0)
            candidates.append((score, conid))

        if not candidates:
            return None
        candidates.sort(key=lambda c: -c[0])
        conid = candidates[0][1]
        self._conid_cache[symbol] = conid
        return conid

    # ---- orders -----------------------------------------------------------

    @staticmethod
    def extract_order_id(resp: dict[str, Any]) -> str:
        """Pull the IBKR order_id from whatever shape place_order_with_replies
        returns (direct ``order_id``/``id``, or nested under ``result``)."""
        direct = resp.get("order_id") or resp.get("id")
        if direct:
            return str(direct)
        result = resp.get("result")
        if isinstance(result, list) and result and isinstance(result[0], dict):
            inner = result[0].get("order_id") or result[0].get("id")
            if inner:
                return str(inner)
        return ""

    async def place_order_with_replies(
        self,
        account_id: str,
        payload: dict[str, Any],
        *,
        auto_ack_messages: set[str] | frozenset[str] | None = None,
    ) -> dict[str, Any]:
        """Submit an order and walk the reply-confirmation chain.

        IBKR may respond with warning objects ``{"id": "<replyId>",
        "message": ["..."], ...}``. For each warning matching the allowlist we
        POST ``{"confirmed": true}`` to ``/iserver/reply/{id}``; anything else
        raises ``IBKROrderHalted``. Capped at 5 loops to avoid infinite bounce.
        """
        await self._ensure_authenticated()
        allowlist = {m.lower() for m in (auto_ack_messages or DEFAULT_AUTO_ACK_MESSAGES)}
        resp = await self._request(
            "POST", f"/iserver/account/{account_id}/orders", json={"orders": [payload]}
        )
        data = resp.json()

        for _ in range(_MAX_REPLY_LOOPS):
            if not isinstance(data, list):
                return data if isinstance(data, dict) else {"result": data}
            warning = data[0] if data else {}
            if not isinstance(warning, dict) or "id" not in warning:
                return {"result": data}
            reply_id = str(warning["id"])
            messages = warning.get("message") or []
            msg = messages[0] if messages else ""
            if not any(ack in msg.lower() for ack in allowlist):
                raise IBKROrderHalted(reason=msg or "unknown warning", reply_id=reply_id)
            resp = await self._request(
                "POST", f"/iserver/reply/{reply_id}", json={"confirmed": True}
            )
            data = resp.json()

        raise IBKROrderHalted(
            reason=f"exceeded {_MAX_REPLY_LOOPS} reply loops",
            reply_id=str(data[0].get("id")) if isinstance(data, list) and data else "",
        )

    async def cancel_order(self, account_id: str, order_id: str) -> dict[str, Any]:
        resp = await self._request("DELETE", f"/iserver/account/{account_id}/order/{order_id}")
        return resp.json()

    async def get_quote(self, conid: int) -> Decimal | None:
        """Last-traded price for a conid via /iserver/marketdata/snapshot.

        Returns ``None`` if the gateway has no quote (paper accounts without a
        market-data sub sometimes return empty). IBKR field 31 = Last Price; the
        endpoint often returns empty on the first call, so we retry once."""
        await self._ensure_authenticated()
        for _ in range(2):
            resp = await self._request(
                "GET", "/iserver/marketdata/snapshot",
                params={"conids": str(conid), "fields": "31"},
            )
            data = resp.json() or []
            if not data:
                continue
            row = data[0] if isinstance(data, list) else {}
            raw = row.get("31")
            if raw is None or raw == "":
                continue
            # Strip leading status chars: C(losed) H(alted) B(id) A(sk).
            s = str(raw).lstrip("CcHhBbAa ").strip()
            try:
                return Decimal(s)
            except (InvalidOperation, ValueError):
                continue
        return None

    async def list_live_orders(self, account_id: str) -> list[dict[str, Any]]:
        resp = await self._request("GET", "/iserver/account/orders")
        orders = (resp.json() or {}).get("orders") or []
        return [o for o in orders if str(o.get("acct", account_id)) == account_id]
