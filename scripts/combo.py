"""Combo-strategy CLI — run premarket + intraday in one shared-capital book.

    python -m scripts.combo --data synthetic
    POLYGON_API_KEY=... python -m scripts.combo --data polygon --days 252
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

from momentum_desk.backtest.providers import PolygonHistory, SyntheticHistory
from momentum_desk.edge.combo import ComboConfig, ComboLeg, run_combo
from momentum_desk.risk import RiskConfig


def _provider(data: str, session: str, days: int):
    if data == "synthetic":
        return SyntheticHistory(days=days, session=session)
    key = os.environ.get("POLYGON_API_KEY") or os.environ.get("MASSIVE_API_KEY")
    if not key:
        raise SystemExit("set POLYGON_API_KEY for --data polygon")
    universe = "active" if session == "intraday" else "gap"
    return PolygonHistory(api_key=key, days=days, universe_mode=universe, max_per_min=0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", choices=["synthetic", "polygon"], default="synthetic")
    ap.add_argument("--days", type=int, default=252)
    ap.add_argument("--equity", type=float, default=25_000.0)
    ap.add_argument("--max-concurrent", type=int, default=5)
    ap.add_argument("--out", default="data/combo.json")
    args = ap.parse_args()

    legs = [
        ComboLeg(name="premarket", provider=_provider(args.data, "premarket", args.days),
                 session="premarket", exit_policy="pct_trail_10"),
        ComboLeg(name="intraday", provider=_provider(args.data, "intraday", args.days),
                 session="intraday", exit_policy="pct_trail_10", rvol_max=20.0),
    ]
    res = run_combo(legs, ComboConfig(max_concurrent=args.max_concurrent),
                    RiskConfig(account_equity=args.equity))

    m = res.metrics
    print(f"\n=== combo [{' + '.join(res.legs)}] · {res.days} days ===")
    print(f"  equity ${res.starting_equity:,.0f} → ${res.final_equity:,.0f} ({m['return_pct']:+.1f}%)")
    print(f"  trades {m['trades']} (taken {res.n_taken}/{res.n_signals}, skipped {res.n_skipped_capacity}) · "
          f"win {m['win_rate']:.1f}% · PF {m['profit_factor']} · exp {m['expectancy_r']:+.3f}R")
    print(f"  max drawdown ${m['max_drawdown']:,.0f} ({m['max_drawdown_pct']:.1f}%)")
    print("  per-leg attribution:")
    for name in res.legs:
        print(f"     {name:<12} {res.leg_trades[name]:>4} trades   pnl ${res.leg_pnl[name]:>+12,.0f}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(asdict(res), indent=2))
    print(f"  → wrote {out}")


if __name__ == "__main__":
    main()
