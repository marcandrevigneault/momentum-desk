"""Phase-3 evaluation gauntlet CLI.

Subjects a candidate strategy (entry config + exit policy) to bootstrap CIs, a
deflated Sharpe, purged walk-forward, regime breakdown and an untouched holdout,
then prints a pass/caution/fail verdict.

    python -m scripts.gauntlet --data synthetic --session intraday
    POLYGON_API_KEY=... python -m scripts.gauntlet --data polygon --session both --days 120 --candidate pct_trail_10
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

from momentum_desk.backtest.providers import PolygonHistory, SyntheticHistory
from momentum_desk.edge.gauntlet import run_gauntlet
from momentum_desk.edge.screen import ScreenConfig


def _provider(data: str, session: str, days: int):
    if data == "synthetic":
        return SyntheticHistory(days=days, session=session)
    key = os.environ.get("POLYGON_API_KEY") or os.environ.get("MASSIVE_API_KEY")
    if not key:
        raise SystemExit("set POLYGON_API_KEY (or MASSIVE_API_KEY) for --data polygon")
    universe = "active" if session == "intraday" else "gap"
    return PolygonHistory(api_key=key, days=days, universe_mode=universe, max_per_min=0)


def _print(r) -> None:
    print(f"\n=== gauntlet · {r.session} · candidate '{r.candidate}' ===")
    print(f"  {r.n_trades} trades over {r.n_days} days | expectancy {r.expectancy_r:+.3f}R | "
          f"daily Sharpe {r.sharpe_daily:+.3f} | skew {r.skew:+.2f} kurt {r.kurt:.2f}")
    print(f"  bootstrap 95% CI [{r.boot_lo:+.3f}, {r.boot_hi:+.3f}]R · P(edge>0)={r.boot_p_pos:.0%}")
    print(f"  PSR(vs0)={r.psr:.0%} · DSR={r.dsr:.0%} (SR* {r.sr_star:+.3f} over {r.n_trials} trials)")
    print(f"  walk-forward OOS {r.wf_oos_exp:+.3f}R · {r.wf_pos_folds}/{len(r.folds)} folds positive")
    for f in r.folds:
        print(f"     fold {f.fold}: select {f.selected:<14} IS {f.is_exp:+.3f} → OOS {f.oos_exp:+.3f} ({f.oos_n} tr)")
    print(f"  regime: {r.months_pos_frac:.0%} of months positive · holdout {r.holdout_exp:+.3f}R ({r.holdout_n} tr)")
    print("  checks:")
    for c in r.checks:
        mark = {"pass": "✓", "caution": "~", "fail": "✗"}[c.status]
        print(f"     {mark} {c.name}: {c.detail}")
    print(f"  VERDICT: {r.verdict}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", choices=["synthetic", "polygon"], default="synthetic")
    ap.add_argument("--session", choices=["premarket", "intraday", "both"], default="intraday")
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--candidate", default=None, help="exit policy to evaluate (default: best by expectancy)")
    ap.add_argument("--n-trials", type=int, default=None, help="trials to deflate against (default: # policies)")
    ap.add_argument("--slippage", type=float, default=0.3)
    ap.add_argument("--out-dir", default="data")
    args = ap.parse_args()

    sessions = ["premarket", "intraday"] if args.session == "both" else [args.session]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for session in sessions:
        cfg = ScreenConfig(session=session)
        r = run_gauntlet(_provider(args.data, session, args.days), cfg,
                         candidate_policy_name=args.candidate, n_trials=args.n_trials,
                         slippage_pct=args.slippage)
        _print(r)
        path = out_dir / f"gauntlet_{session}.json"
        path.write_text(json.dumps(asdict(r), indent=2))
        print(f"  → wrote {path}")


if __name__ == "__main__":
    main()
