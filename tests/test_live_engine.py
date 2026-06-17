"""Reconciliation proof: the streaming live engine, fed bar-by-bar, reproduces
the backtester's trades exactly. This is what lets us trust live results against
the Lab — same entry (_find_event) and same exit (simulate_exit_detail), just
driven incrementally instead of over a whole day at once.

Run with permissive risk + no capacity cap so every signal becomes a backtest
trade — isolating the ENTRY/EXIT logic (sizing/concurrency are a separate layer
the trader applies above the engine, identically to run_simulation).
"""
from __future__ import annotations

from momentum_desk.backtest.providers import SyntheticHistory
from momentum_desk.edge.portfolio import SimConfig, _policy, run_simulation
from momentum_desk.edge.screen import ScreenConfig, _passes_gate
from momentum_desk.live_engine import EntrySignal, ExitSignal, SymbolTracker
from momentum_desk.risk import RiskConfig


def _replay_day(provider, day, cfg, policy, slippage):
    """Feed each gated candidate's bars one at a time through a tracker; collect
    completed (entry, exit) per symbol."""
    out = {}
    for cand in provider.candidates(day):
        if not _passes_gate(cand, cfg):
            continue
        bars = provider.minutes(cand.symbol, day)
        if not bars:
            continue
        t = SymbolTracker(cand, cfg, policy, slippage)
        entry = exit_ = None
        for b in bars:
            sig = t.on_bar(b)
            if isinstance(sig, EntrySignal):
                entry = sig
            elif isinstance(sig, ExitSignal):
                exit_ = sig
        if exit_ is None:
            exit_ = t.end_of_day()
        if entry and exit_:
            out[cand.symbol] = (round(entry.entry, 4), round(exit_.exit_price, 4),
                                exit_.exit_tod, exit_.reason)
    return out


def _run(session: str, exit_policy: str):
    provider = SyntheticHistory(days=60, session=session)
    cfg = ScreenConfig(session=session)
    policy = _policy(exit_policy)
    # backtest: no capacity cap, permissive risk → every signal becomes a trade
    scfg = SimConfig(session=session, exit_policy=exit_policy, max_concurrent=10_000, max_gross_pct=1e12)
    risk = RiskConfig(account_equity=1e9, max_pct_of_recent_volume=100.0,
                      max_position_pct_of_equity=100.0, min_stop_distance_pct=0.0)
    sim = run_simulation(provider, scfg, risk)
    bt = {(t["day"], t["symbol"]): (t["entry"], t["exit"], t["exit_tod"], t["exit_reason"])
          for t in sim.trades}
    eng = {}
    for day in provider.trading_days():
        for sym, v in _replay_day(provider, day, cfg, policy, scfg.slippage_pct).items():
            eng[(day, sym)] = v
    return bt, eng


def test_engine_matches_backtest_intraday_trail():
    bt, eng = _run("intraday", "pct_trail_10")
    assert len(bt) > 30                      # the day must actually produce trades
    assert eng == bt                         # bar-for-bar identical entries + exits


def test_engine_matches_backtest_intraday_fixed_target():
    bt, eng = _run("intraday", "fixed_3r")
    assert len(bt) > 30 and eng == bt


def test_engine_matches_backtest_premarket():
    bt, eng = _run("premarket", "pct_trail_10")
    assert eng == bt


def test_tracker_emits_entry_then_exit_in_order():
    provider = SyntheticHistory(days=10, session="intraday")
    cfg = ScreenConfig(session="intraday")
    day = provider.trading_days()[0]
    seen_entry = False
    for cand in provider.candidates(day):
        if not _passes_gate(cand, cfg):
            continue
        t = SymbolTracker(cand, cfg, _policy("pct_trail_10"))
        order = []
        for b in provider.minutes(cand.symbol, day):
            sig = t.on_bar(b)
            if sig:
                order.append(type(sig).__name__)
        # an exit never precedes an entry
        if "ExitSignal" in order:
            assert order.index("EntrySignal") < order.index("ExitSignal")
            seen_entry = True
    assert seen_entry
