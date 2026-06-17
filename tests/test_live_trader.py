"""The live streaming engine produces exactly the orders the proven dry-run does:
same trackers, same sizing, same risk rejections — so "what it would trade live"
equals the Lab backtest, now computed bar-by-bar off candidates + closed bars."""
from __future__ import annotations

import pytest

from momentum_desk.backtest.providers import SyntheticHistory
from momentum_desk.dryrun import dryrun_day
from momentum_desk.edge.strategy import LegSpec, SizingSpec, Strategy
from momentum_desk.live_trader import LiveEngine, candidate_from_snapshot
from momentum_desk.models import Snapshot


def _key(o: dict) -> tuple:
    return (o["symbol"], o["entry"], o["exit"], o["reason"], o["shares"])


def test_live_engine_orders_match_dryrun():
    strat = Strategy(name="x", kind="single", session="intraday", exit_policy="pct_trail_10",
                     sizing=SizingSpec(mode="fixed", risk_pct=1.0))
    equity = 25_000.0
    provider = SyntheticHistory(days=40, session="intraday")

    live_closed = []
    for day in provider.trading_days():
        eng = LiveEngine(strat, account_equity=equity, day=day)
        for cand in provider.candidates(day):
            eng.register(cand)
            for bar in provider.minutes(cand.symbol, day):
                eng.on_bar(cand.symbol, bar)
        eng.finalize()
        live_closed.extend(eng.closed)
        # dry-run for the same day must produce the identical intended orders
        assert sorted(_key(o) for o in eng.closed) == \
            sorted(_key(o) for o in dryrun_day(provider, day, strat, account_equity=equity))

    assert len(live_closed) > 15            # the synthetic feed actually trades


def test_gate_filters_non_candidates():
    strat = Strategy(name="x", kind="single", session="premarket", exit_policy="fixed_3r")
    eng = LiveEngine(strat, account_equity=25_000.0, day="2026-06-16")
    # premarket gates on the gap; a flat snapshot (open == prev_close) fails it
    flat = Snapshot(symbol="FLAT", last=10.0, prev_close=10.0, day_open=10.0, vwap=10.0,
                    cum_volume=1000, avg_volume_20d=1e6)
    eng.observe(flat)
    assert "FLAT" not in eng.trackers
    # a 20% gapper passes
    gap = Snapshot(symbol="GAP", last=12.0, prev_close=10.0, day_open=12.0, vwap=12.0,
                   cum_volume=1000, avg_volume_20d=1e6)
    eng.observe(gap)
    assert "GAP" in eng.trackers


def test_candidate_from_snapshot_carries_context():
    snap = Snapshot(symbol="AAA", last=5.0, prev_close=4.0, day_open=5.0, vwap=5.0,
                    cum_volume=1000, avg_volume_20d=2e6, float_shares=8e6)
    cand = candidate_from_snapshot(snap, "2026-06-16")
    assert cand.symbol == "AAA" and cand.prev_close == 4.0 and cand.day_open == 5.0
    assert cand.avg_volume_20d == 2e6 and cand.float_shares == 8e6


def test_multi_leg_strategy_rejected():
    with pytest.raises(ValueError):
        LiveEngine(Strategy(name="c", kind="combo", legs=[LegSpec("a", "intraday")]),
                   account_equity=25_000.0, day="2026-06-16")
