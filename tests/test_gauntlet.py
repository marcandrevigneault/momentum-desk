"""Evaluation gauntlet: statistics correctness + end-to-end verdict."""
from __future__ import annotations

import math

from momentum_desk.backtest.providers import SyntheticHistory
from momentum_desk.edge.gauntlet import (
    _expected_max_sharpe,
    _norm_cdf,
    _norm_ppf,
    _psr,
    run_gauntlet,
)
from momentum_desk.edge.screen import ScreenConfig


def test_norm_roundtrip():
    assert abs(_norm_cdf(0.0) - 0.5) < 1e-9
    for p in (0.01, 0.25, 0.5, 0.84, 0.975):
        assert abs(_norm_cdf(_norm_ppf(p)) - p) < 1e-4


def test_psr_monotonic_in_sharpe():
    # higher sample Sharpe → higher probability of beating the benchmark
    low = _psr(0.05, 0.0, 200, 0.0, 3.0)
    high = _psr(0.30, 0.0, 200, 0.0, 3.0)
    assert 0.0 <= low < high <= 1.0


def test_expected_max_sharpe_grows_with_trials():
    # more trials → a higher bar the candidate must clear
    assert _expected_max_sharpe(0.04, 50) > _expected_max_sharpe(0.04, 5) > 0
    assert _expected_max_sharpe(0.0, 100) == 0.0   # no dispersion → no inflation


def test_deflation_lowers_significance():
    # DSR (vs an inflated SR*) must be <= PSR (vs zero) for the same strategy
    sr, n = 0.2, 250
    psr0 = _psr(sr, 0.0, n, 0.0, 3.0)
    sr_star = _expected_max_sharpe(0.05, 30)
    dsr = _psr(sr, sr_star, n, 0.0, 3.0)
    assert dsr <= psr0


def test_run_gauntlet_end_to_end():
    provider = SyntheticHistory(days=120, session="intraday")
    r = run_gauntlet(provider, ScreenConfig(session="intraday"), n_boot=300)
    assert r.n_trades > 0 and r.n_days > 0
    assert len(r.checks) == 5
    assert r.verdict
    assert 0.0 <= r.dsr <= 1.0 and 0.0 <= r.psr <= 1.0
    assert not math.isnan(r.sharpe_daily)
    assert r.folds, "expected walk-forward folds"


def test_gauntlet_rejects_zero_drift_null():
    """NEGATIVE CONTROL (specificity): a true zero-drift random walk has no edge
    by construction — the gauntlet MUST reject it, or its "SURVIVES" verdicts
    carry no information. (The default synthetic is rigged upward and is NOT a
    null — see providers.py.)"""
    null = SyntheticHistory(days=160, session="intraday", null_drift=True)
    r = run_gauntlet(null, ScreenConfig(session="intraday"), n_boot=400)
    assert not r.verdict.startswith("SURVIVES"), f"gauntlet blessed a null: {r.verdict}"
    assert r.dsr < 0.95, f"deflated Sharpe should not clear the bar on noise (got {r.dsr})"
