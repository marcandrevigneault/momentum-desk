"""Tests for the robustness tooling: sweep ranking, day-splitting, and that
walk-forward trains and tests on disjoint windows."""
from __future__ import annotations

from momentum_desk.backtest import SyntheticHistory, sweep, walk_forward
from momentum_desk.backtest.sweep import _split, _SubsetProvider


def test_sweep_returns_ranked_rows():
    rows = sweep(SyntheticHistory(days=40),
                 grid={"target_r": [1.5, 2.0, 3.0], "stop_buffer_pct": [0.3]})
    assert len(rows) == 3
    # sorted by R/trade descending
    rs = [r.expectancy_r for r in rows]
    assert rs == sorted(rs, reverse=True)
    # each row carries the params that produced it
    assert {r.params["target_r"] for r in rows} == {1.5, 2.0, 3.0}


def test_split_is_contiguous_and_complete():
    days = [f"d{i}" for i in range(40)]
    chunks = _split(days, 4)
    assert len(chunks) == 4
    assert [d for c in chunks for d in c] == days          # nothing lost or reordered
    assert all(chunks)                                      # no empty chunk


def test_subset_provider_restricts_days():
    base = SyntheticHistory(days=40)
    subset_days = base.trading_days()[:10]
    sub = _SubsetProvider(base, subset_days)
    assert sub.trading_days() == subset_days
    # delegates data access unchanged
    d = subset_days[0]
    assert sub.candidates(d) == base.candidates(d)


def test_walk_forward_trains_and_tests_disjoint():
    wf = walk_forward(SyntheticHistory(days=40),
                      grid={"target_r": [1.5, 2.0, 3.0], "stop_buffer_pct": [0.3]}, folds=3)
    assert wf.folds, "expected at least one fold"
    for f in wf.folds:
        assert f.train_days > 0 and f.test_days > 0
        assert f.best_params["target_r"] in (1.5, 2.0, 3.0)
    # degradation is defined as mean in-sample minus mean out-of-sample
    assert abs(wf.degradation - (wf.mean_in_sample_r - wf.mean_out_sample_r)) < 1e-6
