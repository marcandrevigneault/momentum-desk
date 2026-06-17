"""Golden contract for the serialized result/trade shapes.

These dataclasses are asdict()'d straight into the JSON the dashboard reads, so
their field NAMES are a frontend contract. This test locks the current shapes so
the step-4 unification (merging BacktestResult/SimResult/ComboResult into one
StrategyResult) can't silently drop or rename a key the UI depends on. Field sets
are asserted (JSON is read by key, so order is irrelevant).
"""
from __future__ import annotations

from dataclasses import fields

from momentum_desk.backtest.data import BacktestResult, Trade
from momentum_desk.edge.combo import ComboResult
from momentum_desk.edge.portfolio import SimResult, SimTrade


def _names(dc) -> set[str]:
    return {f.name for f in fields(dc)}


def test_backtest_result_contract():
    assert _names(BacktestResult) == {
        "metrics", "trades", "equity_curve", "starting_equity", "days", "skipped_no_entry",
    }


def test_trade_contract():
    # the single-backtest Trade (BacktesterPage reads entry_t/exit_t/stop/target)
    assert _names(Trade) == {
        "symbol", "day", "entry_t", "entry", "stop", "target", "shares",
        "exit_t", "exit", "pnl", "r_multiple", "exit_reason",
    }


def test_sim_trade_contract():
    # the account/combo SimTrade (Simulation/Combo pages read entry_tod/exit_tod)
    assert _names(SimTrade) == {
        "day", "symbol", "entry_tod", "exit_tod", "entry", "exit",
        "shares", "pnl", "r_multiple", "exit_reason",
    }


def test_sim_result_contract():
    assert _names(SimResult) == {
        "session", "exit_policy", "days", "starting_equity", "final_equity",
        "n_signals", "n_taken", "n_skipped_capacity",
        "metrics", "equity_curve", "daily_equity", "monthly", "trades",
    }


def test_combo_result_contract():
    assert _names(ComboResult) == {
        "legs", "days", "starting_equity", "final_equity",
        "n_signals", "n_taken", "n_skipped_capacity",
        "metrics", "leg_pnl", "leg_trades", "equity_curve", "daily_equity", "monthly", "trades",
    }


def test_sim_and_combo_share_the_account_run_fields():
    """The fields that should become a shared base when we unify (step 4)."""
    common = _names(SimResult) & _names(ComboResult)
    assert common == {
        "days", "starting_equity", "final_equity", "n_signals", "n_taken",
        "n_skipped_capacity", "metrics", "equity_curve", "daily_equity", "monthly", "trades",
    }
