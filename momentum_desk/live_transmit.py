"""Transmit the live engine's intended orders to the IBKR paper account — the ONE
place a real order is ever sent. Everything here is paper-first and heavily
guarded; the caller (the engine loop) is the single owner that drains the queue.

``transmit_entry`` sends the entry as a market parent plus a broker-resident
protective stop child in one submission, so a crashed desk never leaves a naked
position. ``transmit_exit`` cancels the resting stop (best-effort — it may have
just filled) and closes with a market order. ``decide`` is the pure guard: given
the armed state, the clock, the account, and what's already held, it returns for
each intended event whether to SEND, SKIP, or HALT — so the policy is
unit-testable without a live gateway.
"""
from __future__ import annotations

from dataclasses import dataclass

from .broker.base import OrderSide
from .broker.cp.orders import build_entry_with_stop, build_market_order


@dataclass
class Decision:
    action: str          # "send" | "skip" | "halt"
    side: str | None = None    # "BUY" | "SELL" when action == "send"
    reason: str = ""


def decide(event: dict, *, armed: bool, entries_halted: bool, paper: bool,
           in_window: bool, held: set[str]) -> Decision:
    """Whether to transmit one intended event. Exits are allowed to close a
    position even when entries are halted — but only if the broker still holds
    it (the resting stop may already have closed it). Entries face every guard,
    including "no entry without a stop"."""
    kind = event.get("kind")
    if not armed:
        return Decision("skip", reason="not armed")
    if not paper:
        return Decision("halt", reason="account is not paper (DU) — refusing to transmit")
    if kind == "exit":
        if event.get("symbol", "").upper() not in held:
            return Decision("skip", reason="not held at broker (protective stop may have filled)")
        return Decision("send", side=OrderSide.SELL, reason="closing position")
    if kind != "entry":
        return Decision("skip", reason=f"non-tradeable event ({kind})")
    # entry guards
    if entries_halted:
        return Decision("skip", reason="entries halted (daily-loss breaker)")
    if not in_window:
        return Decision("skip", reason="outside the trading window")
    if event.get("symbol", "").upper() in held:
        return Decision("skip", reason="already held in the live account")
    if event.get("shares", 0) <= 0:
        return Decision("skip", reason="non-positive size")
    if not event.get("stop"):
        return Decision("skip", reason="entry without a stop — refusing to transmit")
    return Decision("send", side=OrderSide.BUY, reason="entry signal")


def order_ids(reply: dict) -> list[str]:
    """All order ids from a placement reply, in submission order (a multi-order
    submission returns one result row per order)."""
    result = reply.get("result")
    rows = result if isinstance(result, list) else [reply]
    ids: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            oid = row.get("order_id") or row.get("id")
            if oid:
                ids.append(str(oid))
    return ids


async def _resolve(client, symbol: str) -> int:
    conid = await client.resolve_conid(symbol)
    if conid is None:
        raise ValueError(f"could not resolve conid for {symbol}")
    return conid


async def transmit_entry(client, account_id: str, symbol: str, quantity: int,
                         stop_price: float) -> dict:
    """Resolve the symbol and place the entry as a bracket: market parent +
    protective stop child. Raises on resolution/placement failure so the caller
    can log and move on."""
    conid = await _resolve(client, symbol)
    payload = build_entry_with_stop(account_id, conid, "BUY", int(quantity), stop_price)
    return await client.place_order_with_replies(account_id, payload)


async def transmit_exit(client, account_id: str, symbol: str, quantity: int,
                        stop_order_id: str | None = None) -> dict:
    """Cancel the resting protective stop (best-effort — it may be gone or just
    filled), then close the position with a market order."""
    conid = await _resolve(client, symbol)
    cancel_result: dict | None = None
    if stop_order_id:
        try:
            cancel_result = await client.cancel_order(account_id, stop_order_id)
        except Exception as e:  # noqa: BLE001 — the close must still go out
            cancel_result = {"error": str(e)}
    reply = await client.place_order_with_replies(
        account_id, build_market_order(account_id, conid, "SELL", int(quantity)))
    if cancel_result is not None:
        reply = {**reply, "stop_cancel": cancel_result}
    return reply
