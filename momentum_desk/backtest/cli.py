"""Run a backtest and print an expectancy report.

    python -m momentum_desk.backtest.cli                  # synthetic data (no key)
    python -m momentum_desk.backtest.cli --days 60 --target-r 3
    python -m momentum_desk.backtest.cli --polygon --days 30   # real, needs POLYGON_API_KEY

Synthetic results are an ENGINE TEST ONLY — the data is fabricated, so the P&L
means nothing. Only a polygon run on real history is evidence of anything, and
even then: past expectancy is not future profit, and live slippage is worse.
"""
from __future__ import annotations

import argparse
import os

from ..scanner import ScanConfig
from .engine import BacktestConfig, Backtester
from .providers import PolygonHistory, SyntheticHistory


def _bar(x: float, width: int = 24) -> str:
    n = max(0, min(width, round(x)))
    return "█" * n + "·" * (width - n)


def main() -> None:
    ap = argparse.ArgumentParser(description="Momentum Desk — backtester")
    ap.add_argument("--polygon", action="store_true", help="use real polygon.io history (needs key)")
    ap.add_argument("--days", type=int, default=40)
    ap.add_argument("--target-r", type=float, default=2.0)
    ap.add_argument("--max-hold", type=int, default=60)
    ap.add_argument("--slippage-pct", type=float, default=0.1)
    ap.add_argument("--no-anti-chase", action="store_true", help="disable the VWAP anti-chase filter")
    ap.add_argument("--trades", type=int, default=12, help="how many sample trades to print")
    args = ap.parse_args()

    if args.polygon:
        key = os.environ.get("POLYGON_API_KEY", "")
        if not key:
            raise SystemExit("POLYGON_API_KEY not set — needed for --polygon")
        provider = PolygonHistory(key, days=args.days)
        synthetic = False
    else:
        provider = SyntheticHistory(days=args.days)
        synthetic = True

    bt = BacktestConfig(
        target_r=args.target_r, max_hold_minutes=args.max_hold,
        slippage_pct=args.slippage_pct, use_anti_chase=not args.no_anti_chase,
    )
    result = Backtester(provider, scan=ScanConfig(), bt=bt).run()
    m = result.metrics

    print(f"\nMomentum Desk · backtest · feed={provider.name} · {result.days} days")
    if synthetic:
        print("⚠ SYNTHETIC DATA — fabricated prices. This validates the engine, NOT the strategy.")
    print("─" * 64)
    if m.trades == 0:
        print("No trades triggered. Loosen filters or check the data provider.")
        return

    pnl_color = "+" if m.total_pnl >= 0 else ""
    start_eq = result.starting_equity
    print(f"  trades            {m.trades}   ({result.skipped_no_entry} candidates filtered/no-trigger)")
    print(f"  win rate          {m.win_rate:.1f}%   {_bar(m.win_rate / 100 * 24)}")
    print(f"  avg win / loss    +${m.avg_win:,.0f}  /  ${m.avg_loss:,.0f}")
    print(f"  profit factor     {m.profit_factor:.2f}   (gross win ${m.gross_profit:,.0f} / loss ${m.gross_loss:,.0f})")
    print(f"  expectancy        {pnl_color}${m.expectancy:,.2f}/trade   ({m.expectancy_r:+.3f} R/trade)")
    print(f"  total P&L         {pnl_color}${m.total_pnl:,.2f}   ({m.return_pct:+.2f}% on ${start_eq:,.0f})")
    print(f"  max drawdown      ${m.max_drawdown:,.2f}   ({m.max_drawdown_pct:.2f}%)")

    verdict = ("POSITIVE expectancy" if m.expectancy_r > 0 else "NEGATIVE expectancy")
    print(f"\n  → {verdict} at {args.target_r:.0f}R target, "
          f"{'anti-chase ON' if bt.use_anti_chase else 'anti-chase OFF'}, "
          f"{args.slippage_pct}% slippage.")

    print(f"\n  sample trades (first {args.trades}):")
    print(f"    {'day':<11}{'sym':<6}{'entry':>8}{'exit':>8}{'shares':>8}{'reason':>9}{'R':>7}{'P&L':>10}")
    for t in result.trades[: args.trades]:
        print(f"    {t.day:<11}{t.symbol:<6}{t.entry:>8.2f}{t.exit:>8.2f}{t.shares:>8}"
              f"{t.exit_reason:>9}{t.r_multiple:>7.2f}{t.pnl:>10.2f}")
    print()


if __name__ == "__main__":
    main()
