"""Build the tuner's eval cache (features + R-per-exit-policy per event) for each
session, so the live variable editor can re-score any config instantly.

    POLYGON_API_KEY=... python -m scripts.build_eval_cache --data polygon --days 252
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from momentum_desk.backtest.providers import PolygonHistory, SyntheticHistory
from momentum_desk.edge.screen import ScreenConfig
from momentum_desk.edge.tuner import CACHE_POLICIES, build_cache


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
    ap.add_argument("--days", type=int, default=252)
    ap.add_argument("--sessions", nargs="+", default=["intraday", "premarket"])
    args = ap.parse_args()

    out = {"generated": "2026-06-16", "days": args.days, "policies": CACHE_POLICIES, "sessions": {}}
    for s in args.sessions:
        cache = build_cache(_prov(args.data, s, args.days), ScreenConfig(session=s))
        out["sessions"][s] = cache
        print(f"  {s}: {len(cache)} events cached")
    path = Path("momentum_desk/edge/eval_cache.json")
    path.write_text(json.dumps(out))
    print(f"  → wrote {path} ({path.stat().st_size/1e6:.1f}MB)")


if __name__ == "__main__":
    main()
