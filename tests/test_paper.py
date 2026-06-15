"""Tests for the paper desk: stop-based sizing, ratcheting trailing stop,
auto-exit on trail vs target, and commission-net P&L."""
from __future__ import annotations

import pytest

from momentum_desk.models import Snapshot
from momentum_desk.paper import PaperDesk
from momentum_desk.risk import RiskConfig, RiskEngine


def _snap(symbol="ABCD", last=5.0):
    return Snapshot(symbol=symbol, last=last, prev_close=4.0, day_open=4.5, vwap=4.6,
                    cum_volume=5_000_000, avg_volume_20d=8e5, float_shares=4e6)


def _desk(target_r=2.0, trail_pct=4.0):
    rk = RiskEngine(RiskConfig(account_equity=25_000, max_risk_per_trade_pct=1.0,
                               max_position_pct_of_equity=100, max_pct_of_recent_volume=100))
    return PaperDesk(rk, target_r=target_r, trail_pct=trail_pct, clock=lambda: 0.0)


def test_open_sizes_from_stop_and_sets_target():
    d = _desk(target_r=2.0)
    r = d.open_position(_snap(), entry=5.0, stop=4.75)   # risk $250 / $0.25 = 1000 sh
    assert r["ok"] and r["shares"] == 1000
    assert d.open["ABCD"].target == pytest.approx(5.5)   # 5 + 2*(5-4.75)


def test_cannot_open_twice():
    d = _desk()
    d.open_position(_snap(), 5.0, 4.75)
    assert d.open_position(_snap(), 5.0, 4.75)["ok"] is False


def test_trailing_stop_ratchets_up_then_exits():
    d = _desk(target_r=20.0, trail_pct=4.0)   # high target so we exercise the trail
    d.open_position(_snap(), entry=5.0, stop=4.75)
    d.update({"ABCD": 6.0})
    assert d.open["ABCD"].stop == pytest.approx(5.76)    # 6 * 0.96
    d.update({"ABCD": 7.0})
    assert d.open["ABCD"].stop == pytest.approx(6.72)    # ratcheted up
    d.update({"ABCD": 6.0})                              # falls back below trail
    assert "ABCD" not in d.open
    t = d.closed[-1]
    assert t.exit_reason == "trail" and t.exit == pytest.approx(6.72)
    # gross (6.72-5)*1000 = 1720, minus $10 commissions
    assert t.pnl == pytest.approx(1710.0)


def test_target_exit():
    d = _desk(target_r=2.0)
    d.open_position(_snap(), entry=5.0, stop=4.75)       # target 5.5
    d.update({"ABCD": 5.6})
    t = d.closed[-1]
    assert t.exit_reason == "target" and t.exit == pytest.approx(5.5)
    assert t.pnl == pytest.approx(490.0)                 # (5.5-5)*1000 - 10


def test_manual_close_is_net_of_commission():
    d = _desk()
    d.open_position(_snap(), entry=5.0, stop=4.75)
    t = d.close_position("ABCD", 5.0, "manual")          # flat price → only fees
    assert t.gross_pnl == pytest.approx(0.0) and t.pnl == pytest.approx(-10.0)


def test_realized_feeds_daily_loss_breaker():
    d = _desk()
    d.risk.config.max_daily_loss_pct = 0.01              # trivially small limit
    d.open_position(_snap(), entry=5.0, stop=4.75)
    d.close_position("ABCD", 4.0, "manual")              # a loss
    assert d.risk.daily_loss_limit_hit                    # breaker tripped by paper result
