"""Tests for the IBKR Client Portal (bravos-style) flow.

No network and no pytest-asyncio dependency: we back the async client with an
``httpx.MockTransport`` and drive coroutines with ``asyncio.run``. Covers the
money-critical bits — auth gating, the order reply-walk (auto-ack vs halt), and
conid disambiguation (the foreign-listing guard).
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

import httpx
import pytest

from momentum_desk.broker.cp import IBKRAuthError, IBKRClient, IBKROrderHalted, orders


def _client(handler) -> IBKRClient:
    transport = httpx.MockTransport(handler)
    ac = httpx.AsyncClient(transport=transport, base_url="https://localhost:5000/v1/api")
    return IBKRClient("https://localhost:5000/v1/api", ac, account_id="DU111")


def run(coro):
    return asyncio.run(coro)


# ---------------- auth gating ----------------
def test_ensure_authenticated_raises_when_not_authed():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"authenticated": False, "connected": True, "competing": False})

    c = _client(handler)
    with pytest.raises(IBKRAuthError):
        run(c.get_positions("DU111"))


def test_auth_status_passthrough():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"authenticated": True, "connected": True, "competing": False})

    c = _client(handler)
    assert run(c.auth_status())["authenticated"] is True


# ---------------- order reply walk ----------------
def _authed(extra):
    """A handler that answers auth/status authed and routes other paths via extra."""
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/iserver/auth/status"):
            return httpx.Response(200, json={"authenticated": True, "connected": True, "competing": False})
        return extra(req)
    return handler


def test_place_order_auto_acks_allowlisted_warning():
    calls = {"orders": 0, "reply": 0}

    def extra(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/orders"):
            calls["orders"] += 1
            # IBKR returns a warning needing confirmation (allowlisted: "without market data")
            return httpx.Response(200, json=[{"id": "reply-1", "message": ["Order placed without market data."]}])
        if "/reply/" in req.url.path:
            calls["reply"] += 1
            return httpx.Response(200, json={"order_id": "OID-9", "order_status": "Submitted"})
        return httpx.Response(404)

    c = _client(_authed(extra))
    payload = orders.build_market_order("DU111", 265598, "BUY", 100)
    resp = run(c.place_order_with_replies("DU111", payload))
    assert IBKRClient.extract_order_id(resp) == "OID-9"
    assert calls == {"orders": 1, "reply": 1}


def test_place_order_halts_on_unknown_warning():
    def extra(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/orders"):
            return httpx.Response(200, json=[{"id": "reply-2", "message": ["Account margin requirements changed."]}])
        return httpx.Response(404)

    c = _client(_authed(extra))
    payload = orders.build_market_order("DU111", 265598, "BUY", 100)
    with pytest.raises(IBKROrderHalted):
        run(c.place_order_with_replies("DU111", payload))


# ---------------- conid disambiguation ----------------
def test_resolve_conid_skips_foreign_listing():
    def extra(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/secdef/search"):
            return httpx.Response(200, json=[
                {"conid": 501785751, "secType": "STK", "currency": "MXN", "description": "MEXI"},
                {"conid": 12345, "secType": "STK", "currency": "USD", "description": "NASDAQ"},
            ])
        return httpx.Response(404)

    c = _client(_authed(extra))
    assert run(c.resolve_conid("IYT")) == 12345   # USD/NASDAQ row wins; MXN rejected


def test_resolve_conid_caches():
    hits = {"n": 0}

    def extra(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/secdef/search"):
            hits["n"] += 1
            return httpx.Response(200, json=[{"conid": 7, "secType": "STK", "currency": "USD", "description": "NYSE"}])
        return httpx.Response(404)

    c = _client(_authed(extra))
    assert run(c.resolve_conid("ABCD")) == 7
    assert run(c.resolve_conid("ABCD")) == 7
    assert hits["n"] == 1   # second call served from the in-memory cache


# ---------------- order builders ----------------
def test_market_order_payload_shape():
    o = orders.build_market_order("DU111", 265598, "BUY", 100)
    assert o["orderType"] == "MKT" and o["side"] == "BUY" and o["conid"] == 265598
    assert o["quantity"] == 100.0 and "cOID" in o


def test_bracket_children_reference_parent():
    legs = orders.build_bracket(
        account_id="DU111", parent_conid=1, side="BUY", quantity=10,
        entry_limit=Decimal("5.0"), stop_loss=Decimal("4.5"), take_profit=Decimal("6.0"),
    )
    parent, stop, tp = legs
    assert stop["parentId"] == parent["cOID"] and tp["parentId"] == parent["cOID"]
    assert stop["side"] == "SELL" and tp["side"] == "SELL"   # opposite of BUY entry
