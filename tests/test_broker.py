"""Tests for the execution layer. The IBKR safety guards and the SimBroker
P&L math are money-critical, so they're covered here. ib_async is NOT required
— the guard tests run before any import of it."""
from __future__ import annotations

import pytest

from momentum_desk.broker import (
    IBKRBroker,
    Order,
    OrderSide,
    OrderType,
    SimBroker,
    entry_order,
    route_plan,
    stop_order,
)
from momentum_desk.models import Snapshot
from momentum_desk.risk import RiskConfig, RiskEngine


def _plan(shares_equity=25_000):
    rk = RiskEngine(RiskConfig(account_equity=shares_equity, max_pct_of_recent_volume=100,
                               max_position_pct_of_equity=100))
    snap = Snapshot(symbol="ABCD", last=5.0, prev_close=4.0, day_open=4.5, vwap=4.6,
                    cum_volume=5_000_000, avg_volume_20d=8e5, float_shares=4e6)
    return rk.plan(snap, entry=5.0, stop=4.75)


# ---------------- IBKR safety guards (no ib_async needed) ----------------
def test_live_port_refused_without_allow_live():
    with pytest.raises(ValueError):
        IBKRBroker(port=7496)            # live TWS port
    with pytest.raises(ValueError):
        IBKRBroker(port=4001)            # live Gateway port


def test_paper_port_ok_and_defaults_to_dry_run():
    b = IBKRBroker(port=7497)
    assert b.is_paper_port and b.dry_run is True


def test_live_port_allowed_only_with_explicit_flag():
    b = IBKRBroker(port=7496, allow_live=True)
    assert not b.is_paper_port and b.allow_live


def test_dry_run_never_transmits():
    b = IBKRBroker(port=7497, dry_run=True)   # not connected, but dry-run short-circuits
    res = b.place_order(Order("ABCD", OrderSide.BUY, 100, OrderType.MKT))
    assert res.status == "dry_run" and res.filled_qty == 0


# ---------------- SimBroker fills + P&L ----------------
def test_sim_fill_and_realized_pnl():
    b = SimBroker()
    b.place_order(Order("ABCD", OrderSide.BUY, 100, OrderType.LMT, limit_price=5.0))
    assert b.positions()[0].quantity == 100
    close = b.place_order(Order("ABCD", OrderSide.SELL, 100, OrderType.LMT, limit_price=5.5))
    assert close.realized_pnl == pytest.approx(50.0)     # (5.5-5.0)*100
    assert b.positions() == []                           # flat again


def test_sim_rejects_unpriced_market_order():
    b = SimBroker()
    res = b.place_order(Order("ABCD", OrderSide.BUY, 100, OrderType.MKT))  # no ref price
    assert res.status == "rejected"


# ---------------- plan → orders → routing ----------------
def test_orders_inherit_size_from_plan():
    plan = _plan()
    assert plan.ok and plan.shares == 1000
    assert entry_order(plan).quantity == 1000
    s = stop_order(plan)
    assert s.side is OrderSide.SELL and s.type is OrderType.STP and s.stop_price == plan.stop


def test_route_plan_sends_entry_then_resting_stop():
    b = SimBroker()
    results = route_plan(b, _plan(), ref_price=5.0)
    # entry fills; the protective stop rests (does NOT execute on submit)
    assert [r.status for r in results] == ["filled", "submitted"]
    assert b.positions()[0].quantity == 1000


def test_route_plan_blocks_rejected_plan():
    rk = RiskEngine(RiskConfig(account_equity=10_000, max_daily_loss_pct=3.0))
    rk.record_fill(-400)  # trip the breaker
    snap = Snapshot(symbol="ABCD", last=5.0, prev_close=4.0, day_open=4.5, vwap=4.6,
                    cum_volume=1_000_000, avg_volume_20d=8e5)
    plan = rk.plan(snap, entry=5.0, stop=4.75)
    b = SimBroker()
    results = route_plan(b, plan, ref_price=5.0)
    assert len(results) == 1 and results[0].status == "rejected"
    assert b.positions() == []   # nothing reached the broker
