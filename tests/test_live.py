"""Autonomous paper loop: hard caps, session window, flatten/kill switch."""
from __future__ import annotations

from momentum_desk.broker.base import OrderType, trail_order
from momentum_desk.broker.sim import SimBroker
from momentum_desk.live import LiveConfig, LivePaperTrader
from momentum_desk.models import Snapshot
from momentum_desk.risk import RiskConfig, RiskEngine
from momentum_desk.scanner import ScanConfig, ScannerEngine


class _Adapter:
    """Feeds a fixed set of actionable snapshots each poll."""
    name = "stub"

    def __init__(self, snaps):
        self._snaps = snaps

    def poll(self):
        return list(self._snaps)


def _snap(sym, last=5.0):
    return Snapshot(symbol=sym, last=last, prev_close=last / 1.5, day_open=last * 0.97,
                    vwap=last * 0.95, cum_volume=5_000_000, avg_volume_20d=300_000,
                    float_shares=8e6, has_news=True)


def _trader(snaps, **cfg):
    scanner = ScannerEngine(ScanConfig(min_relative_volume=2.0, min_gap_pct=5.0, require_news=True))
    risk = RiskEngine(RiskConfig(account_equity=25_000))
    broker = SimBroker()
    return LivePaperTrader(_Adapter(snaps), scanner, risk, broker, LiveConfig(**cfg)), broker


def test_trail_order_is_a_trailing_stop():
    from momentum_desk.risk import PositionPlan, Verdict
    plan = PositionPlan("AAA", Verdict.OK, 100, 5.0, 4.5, 50.0, [])
    o = trail_order(plan, 10.0)
    assert o.type is OrderType.TRAIL and o.trailing_percent == 10.0


def test_enters_within_window_and_caps_concurrency():
    snaps = [_snap(s) for s in ("AAA", "BBB", "CCC", "DDD")]
    trader, broker = _trader(snaps, max_concurrent=2, session_start_tod=570, session_end_tod=660)
    out = trader.step(now_tod=600)              # 10:00 ET, inside the window
    assert len(out["acted"]) == 2              # concurrency cap honoured
    assert len(broker.positions()) == 2


def test_no_entries_outside_window():
    trader, _ = _trader([_snap("AAA")], session_start_tod=570, session_end_tod=660)
    assert trader.step(now_tod=540)["acted"] == []   # 09:00, before the open
    assert trader.step(now_tod=700)["acted"] == []   # 11:40, after the cutoff


def test_one_entry_per_symbol_per_day():
    trader, _ = _trader([_snap("AAA")])
    assert len(trader.step(now_tod=600)["acted"]) == 1
    assert trader.step(now_tod=601)["acted"] == []    # already traded AAA today


def test_max_trades_day_halts_new_entries():
    trader, _ = _trader([_snap("AAA"), _snap("BBB"), _snap("CCC")], max_trades_day=1)
    out = trader.step(now_tod=600)
    assert len(out["acted"]) == 1 and out["trades"] == 1


def test_flatten_closes_everything():
    trader, broker = _trader([_snap("AAA"), _snap("BBB")], max_concurrent=5)
    trader.step(now_tod=600)
    assert len(broker.positions()) == 2
    closed = trader.flatten()
    assert set(closed) == {"AAA", "BBB"}
    assert broker.positions() == []
