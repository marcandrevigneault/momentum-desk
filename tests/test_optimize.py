"""Optimizer: grid evaluation, ranking, and honest deflation of the winner."""
from __future__ import annotations

from momentum_desk.backtest.providers import SyntheticHistory
from momentum_desk.edge.optimize import build_eval_events, default_grid, optimize
from momentum_desk.edge.screen import ScreenConfig


def test_grid_is_large():
    # the search must span enough configs that deflation actually bites
    assert len(default_grid()) >= 100


def test_events_built_once():
    provider = SyntheticHistory(days=60, session="intraday")
    events = build_eval_events(provider, ScreenConfig(session="intraday"), 0.3)
    assert events, "expected some entry events"
    for e in events[:5]:
        assert e.fwd and e.entry > e.init_stop


def test_optimize_ranks_and_deflates():
    provider = SyntheticHistory(days=90, session="intraday")
    res = optimize(provider, ScreenConfig(session="intraday"), slippage_pct=0.3)
    assert res.n_configs >= 100
    assert res.ranked, "expected at least one valid config"
    # ranked by daily Sharpe, descending
    sharpes = [r.daily_sharpe for r in res.ranked]
    assert sharpes == sorted(sharpes, reverse=True)
    # deflation produced a probability and a non-negative bar
    assert 0.0 <= res.deflated_sharpe <= 1.0
    assert res.sr_star >= 0.0
    assert res.note
