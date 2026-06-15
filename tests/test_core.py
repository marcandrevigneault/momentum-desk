"""Tests for the safety-critical logic — the parts where a bug costs real money:
position sizing, the daily-loss circuit breaker, the liquidity guard, the
scanner's anti-chase flags, and the backtest metrics math.

    pip install pytest && pytest -q
"""
from __future__ import annotations

from momentum_desk.backtest import BacktestConfig, Backtester, SyntheticHistory
from momentum_desk.models import Flag, Snapshot
from momentum_desk.risk import RiskConfig, RiskEngine
from momentum_desk.scanner import ScanConfig, ScannerEngine


def _snap(symbol="TEST", last=5.0, cum_volume=5_000_000, **kw):
    base = dict(
        symbol=symbol, last=last, prev_close=4.0, day_open=4.5, vwap=4.6,
        cum_volume=cum_volume, avg_volume_20d=8e5, float_shares=4e6,
        has_news=True, news_headline="x",
    )
    base.update(kw)
    return Snapshot(**base)


# ---------------- risk sizing ----------------
def test_position_size_is_risk_over_stop_distance():
    rk = RiskEngine(RiskConfig(account_equity=25_000, max_risk_per_trade_pct=1.0,
                               max_position_pct_of_equity=100, max_pct_of_recent_volume=100))
    plan = rk.plan(_snap(last=5.0), entry=5.0, stop=4.75)  # risk $250, stop dist $0.25
    assert plan.ok
    assert plan.shares == 1000           # 250 / 0.25
    assert abs(plan.risk_dollars - 250) < 1e-6


def test_stop_above_entry_is_rejected():
    rk = RiskEngine()
    plan = rk.plan(_snap(), entry=5.0, stop=5.5)
    assert not plan.ok


def test_daily_loss_circuit_breaker_blocks_new_trades():
    rk = RiskEngine(RiskConfig(account_equity=10_000, max_daily_loss_pct=3.0))
    assert not rk.daily_loss_limit_hit
    rk.record_fill(-310)                 # past the $300 (3%) limit
    assert rk.daily_loss_limit_hit
    plan = rk.plan(_snap(), entry=5.0, stop=4.75)
    assert not plan.ok
    assert any("daily loss" in r for r in plan.reasons)


def test_liquidity_guard_caps_size_to_volume_fraction():
    # tiny tape: only 100k shares traded; 1% cap => max 1000 shares regardless of risk
    rk = RiskEngine(RiskConfig(account_equity=1_000_000, max_risk_per_trade_pct=50,
                               max_position_pct_of_equity=100, max_pct_of_recent_volume=1.0))
    plan = rk.plan(_snap(last=2.0, cum_volume=100_000), entry=2.0, stop=1.0)
    assert plan.shares == 1000
    assert any("liquidity" in r for r in plan.reasons)


# ---------------- scanner ----------------
def test_scanner_flags_extended_above_vwap():
    sc = ScannerEngine(ScanConfig(min_gap_pct=5, min_relative_volume=2, require_news=False,
                                  max_extension_above_vwap_pct=8))
    # last far above vwap => EXTENDED, not actionable
    snap = _snap(last=6.0, vwap=5.0, prev_close=4.0)  # +20% above vwap
    sig = sc.evaluate(snap)
    assert sig is not None and Flag.EXTENDED in sig.flags and not sig.actionable


def test_scanner_rejects_high_float():
    sc = ScannerEngine(ScanConfig(max_float_millions=20, min_gap_pct=5,
                                  min_relative_volume=2, require_news=False))
    assert sc.evaluate(_snap(float_shares=50e6)) is None


# ---------------- backtest metrics ----------------
def test_backtest_runs_and_metrics_are_consistent():
    res = Backtester(SyntheticHistory(days=40)).run()
    m = res.metrics
    assert m.trades > 0
    assert m.wins + m.losses == m.trades
    assert abs(sum(t.pnl for t in res.trades) - m.total_pnl) < 1.0
    # equity curve starts at starting equity and moves by trade pnl
    assert res.equity_curve[0] == res.starting_equity
    assert abs(res.equity_curve[-1] - (res.starting_equity + m.total_pnl)) < 1.0


def test_more_slippage_never_helps():
    low = Backtester(SyntheticHistory(days=40), bt=BacktestConfig(slippage_pct=0.1)).run()
    high = Backtester(SyntheticHistory(days=40), bt=BacktestConfig(slippage_pct=0.5)).run()
    assert high.metrics.total_pnl <= low.metrics.total_pnl
