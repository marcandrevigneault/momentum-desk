"""Phase-1 edge screener CLI.

Builds the (features, forward-R) event dataset for one or both sessions and
prints a readable per-feature table: information coefficient + bottom/top decile
mean R. Validates on synthetic data (no key) or runs on real cached Massive data.

    python -m scripts.screen_edge --data synthetic --session both
    POLYGON_API_KEY=... python -m scripts.screen_edge --data polygon --session both --days 120
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

from momentum_desk.backtest.providers import PolygonHistory, SyntheticHistory
from momentum_desk.edge.screen import ScreenConfig, run_screen


def _provider(data: str, session: str, days: int):
    if data == "synthetic":
        return SyntheticHistory(days=days, session=session)
    key = os.environ.get("POLYGON_API_KEY") or os.environ.get("MASSIVE_API_KEY")
    if not key:
        raise SystemExit("set POLYGON_API_KEY (or MASSIVE_API_KEY) for --data polygon")
    universe = "active" if session == "intraday" else "gap"
    return PolygonHistory(api_key=key, days=days, universe_mode=universe, max_per_min=0)


def _print_screen(res) -> None:
    print(f"\n=== session: {res.session} | events: {res.n_events} | "
          f"baseline fwd-R: {res.baseline_fwd_r:+.3f} | win-rate: {res.win_rate:.1%} ===")
    if not res.n_events:
        print("  (no events — check universe / data)")
        return
    # IC = vs recent-low-stop R (confounded); ICfix = vs FIXED-% stop (H4, trustworthy);
    # ICret = vs raw % return. Ranked by |ICfix|.
    print(f"  {'feature':<20} {'kind':<8} {'n':>5} {'IC':>7} {'ICfix':>7} {'ICret':>7}")
    for f in res.features:
        print(f"  {f.name:<20} {f.kind:<8} {f.n:>5} {f.ic:>+7.3f} {f.ic_fixed:>+7.3f} {f.ic_ret:>+7.3f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", choices=["synthetic", "polygon"], default="synthetic")
    ap.add_argument("--session", choices=["premarket", "intraday", "regular", "both"], default="both")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--target-r", type=float, default=2.0)
    ap.add_argument("--out-dir", default="data")
    args = ap.parse_args()

    sessions = ["premarket", "intraday"] if args.session == "both" else [args.session]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for session in sessions:
        cfg = ScreenConfig(session=session, target_r=args.target_r)
        provider = _provider(args.data, session, args.days)
        res = run_screen(provider, cfg)
        _print_screen(res)
        path = out_dir / f"edge_screen_{session}.json"
        path.write_text(json.dumps(asdict(res), indent=2))
        print(f"  → wrote {path}")


if __name__ == "__main__":
    main()
