"""Parameter optimizer CLI — search the strategy grid, deflate the winner.

    python -m scripts.optimize --data synthetic --session intraday
    POLYGON_API_KEY=... python -m scripts.optimize --data polygon --session intraday --days 252 --workers 8
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

from momentum_desk.backtest.providers import PolygonHistory, SyntheticHistory
from momentum_desk.edge.optimize import optimize
from momentum_desk.edge.screen import ScreenConfig


def _provider(data: str, session: str, days: int):
    if data == "synthetic":
        return SyntheticHistory(days=days, session=session)
    key = os.environ.get("POLYGON_API_KEY") or os.environ.get("MASSIVE_API_KEY")
    if not key:
        raise SystemExit("set POLYGON_API_KEY (or MASSIVE_API_KEY) for --data polygon")
    universe = "active" if session == "intraday" else "gap"
    return PolygonHistory(api_key=key, days=days, universe_mode=universe, max_per_min=0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", choices=["synthetic", "polygon"], default="synthetic")
    ap.add_argument("--session", choices=["premarket", "intraday", "regular"], default="intraday")
    ap.add_argument("--days", type=int, default=252)
    ap.add_argument("--slippage", type=float, default=0.3)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--out", default="data/optimize.json")
    args = ap.parse_args()

    provider = _provider(args.data, args.session, args.days)
    res = optimize(provider, ScreenConfig(session=args.session), slippage_pct=args.slippage,
                   workers=args.workers)

    print(f"\n=== optimize · {args.session} · {res.n_configs} configs · {res.n_events} events ===")
    print(f"  {'#':>2} {'sharpe':>7} {'expR':>7} {'win%':>6} {'PF':>6} {'n':>5}  config")
    for i, r in enumerate(res.ranked[: args.top], 1):
        pf = "inf" if r.profit_factor == float("inf") else f"{r.profit_factor:.2f}"
        print(f"  {i:>2} {r.daily_sharpe:>+7.3f} {r.expectancy_r:>+7.3f} {r.win_rate * 100:>5.1f}% "
              f"{pf:>6} {r.n:>5}  {r.label}")
    print(f"\n  best: {res.best_label}")
    print(f"  deflated Sharpe {res.deflated_sharpe:.0%} (SR* {res.sr_star:+.3f} over {res.n_configs} trials)")
    print(f"  → {res.note}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(asdict(res), indent=2, default=str))
    print(f"  → wrote {out}")


if __name__ == "__main__":
    main()
