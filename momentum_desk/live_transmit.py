"""Transmit the live engine's intended orders to the IBKR paper account — the ONE
place a real order is ever sent. Everything here is paper-first and heavily
guarded; the caller (the engine loop) is the single owner that drains the queue.

``transmit_order`` is the raw send (resolve conid → market order → place with
warning auto-ack). ``decide`` is the pure guard: given the armed state, the clock,
the account, and what's already held, it returns for each intended event whether
to SEND, SKIP, or HALT — so the policy is unit-testable without a live gateway.
"""
from __future__ import annotations

from dataclasses import dataclass

from .broker.base import OrderSide
from .broker.cp.orders import build_market_order


@dataclass
class Decision:
    action: str          # "send" | "skip" | "halt"
    side: str | None = None    # "BUY" | "SELL" when action == "send"
    reason: str = ""


def decide(event: dict, *, armed: bool, entries_halted: bool, paper: bool,
           in_window: bool, held: set[str]) -> Decision:
    """Whether to transmit one intended event. Exits are always allowed to close
    a position (even when entries are halted); entries face every guard."""
    kind = event.get("kind")
    if not armed:
        return Decision("skip", reason="not armed")
    if not paper:
        return Decision("halt", reason="account is not paper (DU) — refusing to transmit")
    if kind == "exit":
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
    return Decision("send", side=OrderSide.BUY, reason="entry signal")


async def transmit_order(client, account_id: str, symbol: str, side: str,
                         quantity: int) -> dict:
    """Resolve the symbol, build a market order, and place it (auto-acking IBKR's
    benign warnings). Returns the gateway's reply. Raises on resolution/placement
    failure so the caller can log and move on."""
    conid = await client.resolve_conid(symbol)
    if conid is None:
        raise ValueError(f"could not resolve conid for {symbol}")
    payload = build_market_order(account_id, conid, side, int(quantity))
    return await client.place_order_with_replies(account_id, payload)
