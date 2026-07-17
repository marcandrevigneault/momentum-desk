"""The transmission guard (decide) and the raw sends. These are the safety
surface for real paper orders, so they're unit-tested without a live gateway:
every entry carries a broker-resident protective stop, exits cancel the stop and
only fire while the broker still holds the symbol, a non-paper account halts
everything."""
from __future__ import annotations

import asyncio

import pytest

from momentum_desk.live_transmit import (
    decide,
    order_ids,
    transmit_entry,
    transmit_exit,
)

_ENTRY = {"kind": "entry", "symbol": "AAA", "shares": 100, "stop": 4.75}
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


def test_exit_sends_sell_even_when_entries_halted():
    d = decide(_EXIT, **{**_OK, "entries_halted": True, "in_window": False, "held": {"AAA"}})
    assert d.action == "send" and d.side == "SELL"


def test_exit_skipped_when_not_held_at_broker():
    # the resting protective stop may have closed the position already —
    # sending a market SELL then would open a short
    d = decide(_EXIT, **_OK)
    assert d.action == "skip" and "stop" in d.reason


def test_entry_blocked_by_breaker_window_dedup_and_missing_stop():
    assert decide(_ENTRY, **{**_OK, "entries_halted": True}).action == "skip"
    assert decide(_ENTRY, **{**_OK, "in_window": False}).action == "skip"
    assert decide(_ENTRY, **{**_OK, "held": {"AAA"}}).action == "skip"
    assert decide({**_ENTRY, "shares": 0}, **_OK).action == "skip"
    d = decide({**_ENTRY, "stop": None}, **_OK)
    assert d.action == "skip" and "stop" in d.reason


class _FakeClient:
    def __init__(self, conid, cancel_error: Exception | None = None):
        self._conid = conid
        self._cancel_error = cancel_error
        self.placed: list[tuple[str, object]] = []
        self.cancelled: list[tuple[str, str]] = []

    async def resolve_conid(self, symbol):
        return self._conid

    async def place_order_with_replies(self, account_id, payload):
        self.placed.append((account_id, payload))
        orders = payload if isinstance(payload, list) else [payload]
        return {"result": [{"order_id": f"X{i}", "status": "Submitted"}
                           for i, _ in enumerate(orders, start=1)]}

    async def cancel_order(self, account_id, order_id):
        if self._cancel_error is not None:
            raise self._cancel_error
        self.cancelled.append((account_id, order_id))
        return {"msg": "cancelled", "order_id": order_id}


def test_transmit_entry_places_market_parent_with_protective_stop_child():
    client = _FakeClient(conid=12345)
    reply = asyncio.run(transmit_entry(client, "DU111", "AAA", 100, 4.75))
    assert order_ids(reply) == ["X1", "X2"]
    (_, payload), = client.placed
    parent, stop = payload
    assert parent["orderType"] == "MKT" and parent["side"] == "BUY"
    assert parent["conid"] == 12345 and parent["quantity"] == 100
    assert stop["orderType"] == "STP" and stop["side"] == "SELL"
    assert stop["auxPrice"] == 4.75 and stop["quantity"] == 100
    assert stop["parentId"] == parent["cOID"]   # child rides the entry


def test_transmit_exit_cancels_stop_then_sells_market():
    client = _FakeClient(conid=12345)
    reply = asyncio.run(transmit_exit(client, "DU111", "AAA", 100, stop_order_id="S9"))
    assert client.cancelled == [("DU111", "S9")]
    (_, payload), = client.placed
    assert payload["orderType"] == "MKT" and payload["side"] == "SELL"
    assert reply["stop_cancel"]["msg"] == "cancelled"


def test_transmit_exit_still_sells_when_stop_cancel_fails():
    # the stop may have just filled/been purged — the close must still go out
    client = _FakeClient(conid=12345, cancel_error=RuntimeError("gone"))
    reply = asyncio.run(transmit_exit(client, "DU111", "AAA", 100, stop_order_id="S9"))
    assert len(client.placed) == 1
    assert reply["stop_cancel"] == {"error": "gone"}


def test_transmit_entry_raises_on_unresolvable_symbol():
    with pytest.raises(ValueError):
        asyncio.run(transmit_entry(_FakeClient(conid=None), "DU111", "ZZZ", 100, 4.75))
