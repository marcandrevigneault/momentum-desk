"""Full end-to-end account simulation of the assembled strategy.

Detect → size (RiskEngine) → enter → trail → exit, with concurrent-position and
capital caps, over a window (default the last ~year). Prints the equity result.

    python -m scripts.simulate_year --data synthetic --session intraday
    POLYGON_API_KEY=... python -m scripts.simulate_year --data polygon --session intraday --days 252
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

from momentum_desk.backtest.providers import PolygonHistory, SyntheticHistory
from momentum_desk.edge.portfolio import SimConfig, run_simulation
from momentum_desk.risk import RiskConfig


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
    ap.add_argument("--policy", default="pct_trail_10")
    ap.add_argument("--days", type=int, default=252)
    ap.add_argument("--equity", type=float, default=25_000.0)
    ap.add_argument("--risk-pct", type=float, default=1.0)
    ap.add_argument("--max-concurrent", type=int, default=5)
    ap.add_argument("--slippage", type=float, default=0.3)
    ap.add_argument("--compound", action="store_true",
                    help="risk a %% of CURRENT equity each trade (compounds) instead of the fixed start balance")
    ap.add_argument("--out", default="data/sim_year.json")
    args = ap.parse_args()

    provider = _provider(args.data, args.session, args.days)
    scfg = SimConfig(session=args.session, exit_policy=args.policy, slippage_pct=args.slippage,
                     max_concurrent=args.max_concurrent)
    rcfg = RiskConfig(account_equity=args.equity, max_risk_per_trade_pct=args.risk_pct,
                      compound=args.compound)
    res = run_simulation(provider, scfg, rcfg)

    m = res.metrics
    print(f"\n=== {args.session} · {args.policy} · {res.days} days ===")
    print(f"  equity  ${res.starting_equity:,.0f} → ${res.final_equity:,.0f}  "
          f"({m['return_pct']:+.1f}%)")
    print(f"  signals {res.n_signals} · taken {res.n_taken} · skipped (capacity) {res.n_skipped_capacity}")
    print(f"  trades {m['trades']} · win {m['win_rate']:.1f}% · PF {m['profit_factor']} · "
          f"expectancy {m['expectancy_r']:+.3f}R")
    print(f"  max drawdown ${m['max_drawdown']:,.0f} ({m['max_drawdown_pct']:.1f}%)")
    print("  monthly:")
    for row in res.monthly:
        print(f"     {row['period']}  {row['trades']:>3} tr  win {row['win_rate']:>5.1f}%  "
              f"pnl {row['pnl']:>+10,.0f}  cum {row['cum_pnl']:>+10,.0f}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(asdict(res), indent=2))
    print(f"  → wrote {out}")


if __name__ == "__main__":
    main()
