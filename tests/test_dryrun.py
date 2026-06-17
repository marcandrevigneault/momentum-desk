"""The dry-run intended orders match what the backtest takes — same engine, same
sizing, same risk rejections — so "what it would trade live" equals the Lab."""
from __future__ import annotations

from momentum_desk.backtest.providers import SyntheticHistory
from momentum_desk.dryrun import dryrun_day, strategy_to_engine, supported
from momentum_desk.edge.portfolio import SimConfig, run_simulation
from momentum_desk.edge.strategy import LegSpec, SizingSpec, Strategy
from momentum_desk.risk import RiskConfig


def test_bridge_maps_session_and_exit():
    cfg, policy = strategy_to_engine(Strategy(name="x", session="premarket", exit_policy="fixed_3r"))
    assert cfg.session == "premarket" and policy.name == "fixed_3r"


def test_combos_not_supported():
    assert supported(Strategy(name="s", kind="single")) is True
    assert supported(Strategy(name="c", kind="combo", legs=[LegSpec("a", "intraday")])) is False


def test_dryrun_orders_match_backtest():
    strat = Strategy(name="x", kind="single", session="intraday", exit_policy="pct_trail_10",
                     sizing=SizingSpec(mode="fixed", risk_pct=1.0))
    equity = 25_000.0
    provider = SyntheticHistory(days=50, session="intraday")

    intended = []
    for day in provider.trading_days():
        for o in dryrun_day(provider, day, strat, account_equity=equity):
            assert "exit" in o                       # every intended order is a completed entry+exit
            intended.append((day, o["symbol"], o["entry"], o["exit"], o["reason"]))

    # backtest with the SAME risk config but no concurrency cap → it takes every
    # signal the dry-run does (sizing/risk rejections still apply identically)
    scfg = SimConfig(session="intraday", exit_policy="pct_trail_10", max_concurrent=10_000, max_gross_pct=1e12)
    sim = run_simulation(provider, scfg, RiskConfig(account_equity=equity, max_risk_per_trade_pct=1.0))
    bt = [(t["day"], t["symbol"], t["entry"], t["exit"], t["exit_reason"]) for t in sim.trades]

    assert len(bt) > 20
    assert sorted(intended) == sorted(bt)
