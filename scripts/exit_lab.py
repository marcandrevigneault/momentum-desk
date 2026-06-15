"""Phase-2 exit-policy lab CLI.

Holds the entry fixed (same triggers as the screener) and runs every exit policy
through the same trades, then prints a head-to-head comparison ranked by
expectancy (R/trade). Differences are attributable purely to the exit.

    python -m scripts.exit_lab --data synthetic --session both
    POLYGON_API_KEY=... python -m scripts.exit_lab --data polygon --session both --days 120
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

from momentum_desk.backtest.providers import PolygonHistory, SyntheticHistory
from momentum_desk.edge.exits import run_exit_lab
from momentum_desk.edge.screen import ScreenConfig


def _provider(data: str, session: str, days: int):
    if data == "synthetic":
        return SyntheticHistory(days=days, session=session)
    key = os.environ.get("POLYGON_API_KEY") or os.environ.get("MASSIVE_API_KEY")
    if not key:
        raise SystemExit("set POLYGON_API_KEY (or MASSIVE_API_KEY) for --data polygon")
    universe = "active" if session == "intraday" else "gap"
    return PolygonHistory(api_key=key, days=days, universe_mode=universe, max_per_min=0)


def _print(res) -> None:
    print(f"\n=== session: {res.session} | events: {res.n_events} ===")
    if not res.n_events:
        print("  (no events)")
        return
    print(f"  {'policy':<16} {'expR':>7} {'win%':>6} {'PF':>6} {'avgW':>6} {'avgL':>6} "
          f"{'maxDDr':>7} {'hold':>5}  exits")
    for m in res.policies:
        pf = "inf" if m.profit_factor == float("inf") else f"{m.profit_factor:.2f}"
        reasons = ",".join(f"{k}:{v}" for k, v in sorted(m.exit_reasons.items()))
        print(f"  {m.policy:<16} {m.expectancy_r:>+7.3f} {m.win_rate * 100:>5.1f}% {pf:>6} "
              f"{m.avg_win_r:>+6.2f} {m.avg_loss_r:>+6.2f} {m.max_dd_r:>7.1f} {m.avg_hold_bars:>5.0f}  {reasons}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", choices=["synthetic", "polygon"], default="synthetic")
    ap.add_argument("--session", choices=["premarket", "intraday", "regular", "both"], default="both")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--slippage", type=float, default=0.3)
    ap.add_argument("--out-dir", default="data")
    args = ap.parse_args()

    sessions = ["premarket", "intraday"] if args.session == "both" else [args.session]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for session in sessions:
        cfg = ScreenConfig(session=session)
        res = run_exit_lab(_provider(args.data, session, args.days), cfg, slippage_pct=args.slippage)
        _print(res)
        path = out_dir / f"exit_lab_{session}.json"
        path.write_text(json.dumps(asdict(res), indent=2))
        print(f"  → wrote {path}")


if __name__ == "__main__":
    main()
