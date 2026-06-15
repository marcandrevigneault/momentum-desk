"""End-to-end portfolio simulation: invariants + concurrency/capital caps."""
from __future__ import annotations

from momentum_desk.backtest.providers import SyntheticHistory
from momentum_desk.edge.exits import POLICIES, simulate_exit_detail
from momentum_desk.edge.portfolio import SimConfig, run_simulation
from momentum_desk.risk import RiskConfig


def test_exit_detail_matches_r_and_reports_time():
    from momentum_desk.backtest.data import MinuteBar
    bars = [MinuteBar(t=0, o=10, h=10, l=10, c=10, v=1, cum_volume=1, vwap=10, tod=570)]
    fwd = [MinuteBar(t=1, o=10, h=12.5, l=10, c=12.4, v=1, cum_volume=2, vwap=11, tod=575)]
    f = simulate_exit_detail(10.0, 9.0, bars, fwd, POLICIES[1], 0.0)  # fixed_2r
    assert f.reason == "target" and f.r == 2.0
    assert f.exit_price == 12.0 and f.exit_tod == 575


def test_simulation_end_to_end():
    provider = SyntheticHistory(days=60, session="intraday")
    res = run_simulation(provider, SimConfig(session="intraday"), RiskConfig(account_equity=25_000))
    assert res.days == 60
    assert res.n_signals >= res.n_taken            # can't take more than fire
    assert res.starting_equity == 25_000
    assert len(res.daily_equity) == 60
    assert res.metrics["trades"] == res.n_taken
    # equity curve starts at the opening balance
    assert res.equity_curve[0] == 25_000


def test_concurrency_cap_reduces_trades():
    provider = SyntheticHistory(days=60, session="intraday")
    loose = run_simulation(provider, SimConfig(session="intraday", max_concurrent=10), RiskConfig())
    tight = run_simulation(provider, SimConfig(session="intraday", max_concurrent=1), RiskConfig())
    # a tighter concurrency cap can only take the same or fewer trades
    assert tight.n_taken <= loose.n_taken
