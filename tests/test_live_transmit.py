"""The transmission guard (decide) and the raw send. These are the safety surface
for real paper orders, so they're unit-tested without a live gateway: exits always
close, entries face every guard, a non-paper account halts everything."""
from __future__ import annotations

import asyncio

import pytest

from momentum_desk.live_transmit import decide, transmit_order

_ENTRY = {"kind": "entry", "symbol": "AAA", "shares": 100}
_EXIT = {"kind": "exit", "symbol": "AAA", "shares": 100}
_OK = dict(armed=True, entries_halted=False, paper=True, in_window=True, held=set())


def test_not_armed_skips():
    assert decide(_ENTRY, **{**_OK, "armed": False}).action == "skip"


def test_non_paper_account_halts():
    d = decide(_ENTRY, **{**_OK, "paper": False})
    assert d.action == "halt" and "paper" in d.reason


def test_valid_entry_sends_buy():
    d = decide(_ENTRY, **_OK)
    assert d.action == "send" and d.side == "BUY"


def test_exit_always_sends_sell_even_when_halted():
    d = decide(_EXIT, **{**_OK, "entries_halted": True, "in_window": False})
    assert d.action == "send" and d.side == "SELL"


def test_entry_blocked_by_breaker_window_and_dedup():
    assert decide(_ENTRY, **{**_OK, "entries_halted": True}).action == "skip"
    assert decide(_ENTRY, **{**_OK, "in_window": False}).action == "skip"
    assert decide(_ENTRY, **{**_OK, "held": {"AAA"}}).action == "skip"
    assert decide({**_ENTRY, "shares": 0}, **_OK).action == "skip"


class _FakeClient:
    def __init__(self, conid):
        self._conid = conid
        self.placed = None

    async def resolve_conid(self, symbol):
        return self._conid

    async def place_order_with_replies(self, account_id, payload):
        self.placed = (account_id, payload)
        return {"order_id": "X1", "status": "Submitted"}


def test_transmit_order_builds_and_places():
    client = _FakeClient(conid=12345)
    reply = asyncio.run(transmit_order(client, "DU111", "AAA", "BUY", 100))
    assert reply["status"] == "Submitted"
    acct, payload = client.placed
    assert acct == "DU111" and payload["conid"] == 12345
    assert payload["side"] == "BUY" and payload["orderType"] == "MKT" and payload["quantity"] == 100


def test_transmit_order_raises_on_unresolvable_symbol():
    with pytest.raises(ValueError):
        asyncio.run(transmit_order(_FakeClient(conid=None), "DU111", "ZZZ", "BUY", 100))
