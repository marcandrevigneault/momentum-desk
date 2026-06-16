"""Backtest the preset AND/OR entry+exit rule combos for a session and write
momentum_desk/edge/rules_snapshot.json for the Rules page.

    python -m scripts.rules_all --data synthetic --session intraday
    POLYGON_API_KEY=... python -m scripts.rules_all --data polygon --session intraday --days 252
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

from momentum_desk.backtest.providers import PolygonHistory, SyntheticHistory
from momentum_desk.edge.rules import run_presets
from momentum_desk.edge.screen import ScreenConfig


def _prov(data, session, days):
    if data == "synthetic":
        return SyntheticHistory(days=days, session=session)
    key = os.environ.get("POLYGON_API_KEY") or os.environ.get("MASSIVE_API_KEY")
    if not key:
        raise SystemExit("set POLYGON_API_KEY for --data polygon")
    return PolygonHistory(api_key=key, days=days,
                          universe_mode="active" if session == "intraday" else "gap", max_per_min=0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", choices=["synthetic", "polygon"], default="synthetic")
    ap.add_argument("--session", default="intraday")
    ap.add_argument("--days", type=int, default=252)
    args = ap.parse_args()

    cfg = ScreenConfig(session=args.session)
    results = run_presets(_prov(args.data, args.session, args.days), cfg)
    out = {"generated": "2026-06-16", "session": args.session, "days": args.days,
           "results": [asdict(r) for r in results]}
    print(f"\n=== AND/OR rule combos · {args.session} · {args.days} days ===")
    print(f"  {'rule':<26} {'exit':<13} {'n':>5} {'expR':>7} {'win%':>6} {'PF':>6} {'sharpe':>7}")
    for r in results:
        pf = "inf" if r.profit_factor == float("inf") else f"{r.profit_factor:.2f}"
        print(f"  {r.name:<26} {r.exit_policy:<13} {r.n:>5} {r.expectancy_r:>+7.3f} "
              f"{r.win_rate*100:>5.1f}% {pf:>6} {r.daily_sharpe:>+7.3f}")
    path = Path("momentum_desk/edge/rules_snapshot.json")
    path.write_text(json.dumps(out, indent=2))
    print(f"  → wrote {path}")


if __name__ == "__main__":
    main()
