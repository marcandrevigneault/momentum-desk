"""Robustness tooling — because one equity curve proves nothing.

`sweep` grids over strategy parameters and ranks them. But the best in-sample
parameters are usually a fluke: they fit the noise of the days you tested on.
`walk_forward` is the honest check — it picks the best parameters on past days,
then measures them on *future, unseen* days (expanding window). If out-of-sample
expectancy collapses versus in-sample, the strategy is overfit, not real.

    python -m momentum_desk.backtest.sweep --days 60
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, replace

from ..scanner import ScanConfig
from .data import HistoricalProvider
from .engine import BacktestConfig, Backtester

# A small, sane default grid. Keep it modest — every combo is a full backtest.
DEFAULT_GRID: dict[str, list] = {
    "target_r": [1.5, 2.0, 3.0],
    "stop_buffer_pct": [0.1, 0.3],
    "max_hold_minutes": [30, 60],
}


class _SubsetProvider:
    """Wraps a provider to expose only a chosen subset of trading days. Lets the
    walk-forward train and test on disjoint date ranges without copying data."""

    def __init__(self, base: HistoricalProvider, days: list[str]) -> None:
        self._base = base
        self._days = days
        self.name = f"{base.name}[{len(days)}d]"

    def trading_days(self) -> list[str]:
        return list(self._days)

    def candidates(self, day: str):
        return self._base.candidates(day)

    def minutes(self, symbol: str, day: str):
        return self._base.minutes(symbol, day)


@dataclass
class SweepRow:
    params: dict
    expectancy_r: float
    expectancy: float
    total_pnl: float
    trades: int
    win_rate: float
    profit_factor: float
    max_drawdown_pct: float


def _config(base: BacktestConfig, params: dict) -> BacktestConfig:
    return replace(base, **params)


def _row(params: dict, result) -> SweepRow:
    m = result.metrics
    return SweepRow(
        params=params, expectancy_r=m.expectancy_r, expectancy=m.expectancy,
        total_pnl=m.total_pnl, trades=m.trades, win_rate=m.win_rate,
        profit_factor=m.profit_factor, max_drawdown_pct=m.max_drawdown_pct,
    )


def sweep(
    provider: HistoricalProvider,
    grid: dict[str, list] | None = None,
    scan: ScanConfig | None = None,
    base: BacktestConfig | None = None,
) -> list[SweepRow]:
    """Run every parameter combination; return rows ranked by R/trade desc."""
    grid = grid or DEFAULT_GRID
    base = base or BacktestConfig()
    keys = list(grid)
    rows: list[SweepRow] = []
    for combo in itertools.product(*(grid[k] for k in keys)):
        params = dict(zip(keys, combo, strict=True))
        result = Backtester(provider, scan=scan, bt=_config(base, params)).run()
        rows.append(_row(params, result))
    rows.sort(key=lambda r: r.expectancy_r, reverse=True)
    return rows


@dataclass
class Fold:
    train_days: int
    test_days: int
    best_params: dict
    in_sample_r: float       # R/trade of best params on the train window
    out_sample_r: float      # same params on the unseen test window
    out_sample_pnl: float
    out_sample_trades: int


@dataclass
class WalkForward:
    folds: list[Fold]
    mean_out_sample_r: float
    mean_in_sample_r: float
    degradation: float       # IS − OOS; large positive ⇒ overfit


def walk_forward(
    provider: HistoricalProvider,
    grid: dict[str, list] | None = None,
    folds: int = 4,
    scan: ScanConfig | None = None,
    base: BacktestConfig | None = None,
) -> WalkForward:
    """Expanding-window walk-forward: for each fold, optimise on all prior days,
    then score those params on the next unseen block."""
    grid = grid or DEFAULT_GRID
    base = base or BacktestConfig()
    days = provider.trading_days()
    if len(days) < (folds + 1) * 2:
        folds = max(1, len(days) // 4 - 1)
    chunks = _split(days, folds + 1)

    out: list[Fold] = []
    for i in range(1, len(chunks)):
        train_days = [d for c in chunks[:i] for d in c]
        test_days = chunks[i]
        if not train_days or not test_days:
            continue
        best = sweep(_SubsetProvider(provider, train_days), grid, scan, base)[0]
        test_res = Backtester(_SubsetProvider(provider, test_days), scan=scan,
                              bt=_config(base, best.params)).run()
        out.append(Fold(
            train_days=len(train_days), test_days=len(test_days), best_params=best.params,
            in_sample_r=best.expectancy_r, out_sample_r=test_res.metrics.expectancy_r,
            out_sample_pnl=test_res.metrics.total_pnl, out_sample_trades=test_res.metrics.trades,
        ))

    mean_oos = round(sum(f.out_sample_r for f in out) / len(out), 3) if out else 0.0
    mean_is = round(sum(f.in_sample_r for f in out) / len(out), 3) if out else 0.0
    return WalkForward(folds=out, mean_out_sample_r=mean_oos, mean_in_sample_r=mean_is,
                       degradation=round(mean_is - mean_oos, 3))


def _split(items: list, n: int) -> list[list]:
    """Split a list into n roughly-equal contiguous chunks."""
    n = max(1, min(n, len(items)))
    size, rem = divmod(len(items), n)
    out, i = [], 0
    for k in range(n):
        step = size + (1 if k < rem else 0)
        out.append(items[i:i + step])
        i += step
    return out


def _main() -> None:
    import argparse
    import os

    from .providers import PolygonHistory, SyntheticHistory

    ap = argparse.ArgumentParser(description="Momentum Desk — parameter sweep + walk-forward")
    ap.add_argument("--polygon", action="store_true")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--folds", type=int, default=4)
    ap.add_argument("--session", choices=["regular", "premarket"], default="regular")
    args = ap.parse_args()

    if args.polygon:
        key = os.environ.get("POLYGON_API_KEY", "")
        if not key:
            raise SystemExit("POLYGON_API_KEY not set — needed for --polygon")
        provider = PolygonHistory(key, days=args.days)
        synthetic = False
    else:
        provider = SyntheticHistory(days=args.days, session=args.session)
        synthetic = True

    base = BacktestConfig(session=args.session)
    print(f"\nMomentum Desk · sweep · feed={provider.name} · {args.days} days · {args.session}")
    if synthetic:
        print("⚠ SYNTHETIC DATA — engine test only; these numbers are not strategy evidence.")
    print("─" * 70)

    rows = sweep(provider, base=base)
    print("  rank by R/trade:")
    print(f"    {'target_r':>9}{'stop%':>7}{'hold':>6}{'trades':>8}{'win%':>7}{'PF':>7}{'R/trade':>9}")
    for r in rows:
        print(f"    {r.params['target_r']:>9}{r.params['stop_buffer_pct']:>7}"
              f"{r.params['max_hold_minutes']:>6}{r.trades:>8}{r.win_rate:>7.1f}"
              f"{r.profit_factor:>7.2f}{r.expectancy_r:>9.3f}")

    wf = walk_forward(provider, folds=args.folds, base=base)
    print("\n  walk-forward (best-on-past, scored-on-unseen-future):")
    print(f"    {'train':>6}{'test':>6}{'IS R':>8}{'OOS R':>8}{'OOS trades':>12}  best params")
    for f in wf.folds:
        print(f"    {f.train_days:>6}{f.test_days:>6}{f.in_sample_r:>8.3f}{f.out_sample_r:>8.3f}"
              f"{f.out_sample_trades:>12}  {f.best_params}")
    print(f"\n  mean IS R {wf.mean_in_sample_r:+.3f} · mean OOS R {wf.mean_out_sample_r:+.3f} "
          f"· degradation {wf.degradation:+.3f}")
    if wf.degradation > 0.2:
        print("  ⚠ large in→out drop: the good in-sample params look overfit.")
    print()


if __name__ == "__main__":
    _main()
