"""Dry-run the Lab's ACTIVE strategy on the most recent real trading day(s) — the
reconciled live engine + sizing off your paper-account NAV, transmitting NOTHING.
Review this before ever enabling live autotrade.

    POLYGON_API_KEY=... python -m scripts.lab_dryrun
    POLYGON_API_KEY=... python -m scripts.lab_dryrun --days 3 --equity 25000 --strategy "Intraday momentum"

Equity: --equity if given, else the live IBKR paper NAV (if the gateway is up),
else 25,000.
"""
from __future__ import annotations

import argparse
import os

from momentum_desk.backtest.providers import PolygonHistory
from momentum_desk.dryrun import dryrun_day, supported
from momentum_desk.edge.store import LabStore


def _equity(arg: float | None) -> float:
    if arg is not None:
        return arg
    try:
        from momentum_desk.broker import IBKRCPBroker
        b = IBKRCPBroker(gateway_url=os.environ.get("IBKR_GATEWAY_URL", "https://localhost:5000/v1/api"),
                         account_id=os.environ.get("IBKR_ACCOUNT_ID", ""))
        b.connect()
        nav = b.nav()
        b.disconnect()
        if nav:
            print(f"  (sizing off live paper NAV ${nav:,.0f})")
            return float(nav)
    except Exception as e:  # noqa: BLE001
        print(f"  (no gateway NAV: {e}; using fallback equity)")
    return 25_000.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1, help="how many recent trading days to dry-run")
    ap.add_argument("--equity", type=float, default=None)
    ap.add_argument("--strategy", default=None, help="default: the Lab's active strategy")
    args = ap.parse_args()

    from momentum_desk.edge.lab import seed
    store = LabStore(os.environ.get("LAB_DB", "data/lab.db"))
    seed(store)   # ensure the canonical + sweep strategies exist
    name = args.strategy or store.get_active()
    if not name:
        raise SystemExit("no active strategy — pick one (★) in the Lab, or pass --strategy")
    strat = store.get_strategy(name)
    if strat is None:
        raise SystemExit(f"unknown strategy {name!r}")
    if not supported(strat):
        raise SystemExit(f"dry-run supports single-leg strategies only ({name} is multi-leg)")

    key = os.environ.get("POLYGON_API_KEY") or os.environ.get("MASSIVE_API_KEY")
    if not key:
        raise SystemExit("set POLYGON_API_KEY for real-data dry-run")
    equity = _equity(args.equity)
    provider = PolygonHistory(api_key=key, days=max(args.days, 5), universe_mode="active", max_per_min=0)

    print("=" * 70)
    print(f"  DRY RUN · {name} · {strat.session} · {strat.exit_policy} · "
          f"{strat.sizing.mode} {strat.sizing.risk_pct}% of ${equity:,.0f}")
    print("  reconciled engine (== backtest) — NOTHING TRANSMITTED")
    print("=" * 70)
    for day in provider.trading_days()[-args.days:]:
        orders = dryrun_day(provider, day, strat, account_equity=equity)
        pnl = sum(o.get("pnl", 0.0) for o in orders)
        print(f"\n  {day}: {len(orders)} intended trades · day P&L ${pnl:+,.0f}")
        for o in orders:
            print(f"    {o['symbol']:6} BUY {o['shares']:>5} @ {o['entry']:<8} stop {o['stop']:<8} "
                  f"-> exit {o.get('exit', '—'):<8} {o.get('reason', ''):6} "
                  f"R {o.get('r', 0):+.2f}  P&L ${o.get('pnl', 0):+,.0f}")


if __name__ == "__main__":
    main()
