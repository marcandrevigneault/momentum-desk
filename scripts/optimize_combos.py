"""Optimize each combo's parameters (per-leg exit × max-concurrent) and report
the best config — and whether any combo beats intraday-alone. Writes
momentum_desk/edge/combos_optimize_snapshot.json (drives the combo 'optimized' badge).

    POLYGON_API_KEY=... python -m scripts.optimize_combos --days 252
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
from pathlib import Path

from momentum_desk.backtest.providers import PolygonHistory, SyntheticHistory
from momentum_desk.edge.combo import ComboConfig, ComboLeg, run_combo
from momentum_desk.risk import RiskConfig


def _prov(data, session, days):
    if data == "synthetic":
        return SyntheticHistory(days=days, session=session)
    key = os.environ.get("POLYGON_API_KEY") or os.environ.get("MASSIVE_API_KEY")
    if not key:
        raise SystemExit("set POLYGON_API_KEY for --data polygon")
    return PolygonHistory(api_key=key, days=days,
                          universe_mode="active" if session == "intraday" else "gap", max_per_min=0)


def _sharpe(daily_equity):
    eq = [d["equity"] for d in daily_equity]
    rets = [eq[i] - eq[i - 1] for i in range(1, len(eq))]
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return round(mean / var ** 0.5, 4) if var > 0 else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", choices=["synthetic", "polygon"], default="synthetic")
    ap.add_argument("--days", type=int, default=252)
    args = ap.parse_args()

    # leg factories so each config gets fresh providers (sharing the disk cache)
    def intraday(exit_p):
        return ComboLeg(name="intraday", provider=_prov(args.data, "intraday", args.days),
                        session="intraday", exit_policy=exit_p, rvol_max=20.0)

    def premarket(exit_p):
        return ComboLeg(name="premarket", provider=_prov(args.data, "premarket", args.days),
                        session="premarket", exit_policy=exit_p)

    def fade():
        return ComboLeg(name="fade", provider=_prov(args.data, "intraday", args.days),
                        session="intraday", style="fade", exit_policy="pct_trail_10", slippage_pct=0.5)

    leg_sets = {
        "intraday": lambda ex: [intraday(ex)],
        "premkt_intraday": lambda ex: [premarket("pct_trail_10"), intraday(ex)],
        "three_leg": lambda ex: [premarket("pct_trail_10"), intraday(ex), fade()],
    }
    exits = ["pct_trail_10", "fixed_3r"]
    concurrents = [3, 5, 8]

    results = []
    for combo, mk in leg_sets.items():
        for ex, mc in itertools.product(exits, concurrents):
            res = run_combo(mk(ex), ComboConfig(max_concurrent=mc), RiskConfig(account_equity=25_000))
            m = res.metrics
            sh = _sharpe(res.daily_equity)
            results.append({"combo": combo, "intraday_exit": ex, "max_concurrent": mc,
                            "final_equity": res.final_equity, "profit_factor": m["profit_factor"],
                            "expectancy_r": m["expectancy_r"], "max_drawdown_pct": m["max_drawdown_pct"],
                            "daily_sharpe": sh})
            print(f"  {combo:<16} exit={ex:<13} mc={mc}  ${res.final_equity:>10,.0f}  "
                  f"PF {m['profit_factor']:>5.2f}  sharpe {sh:>+7.3f}")

    results.sort(key=lambda r: r["daily_sharpe"], reverse=True)
    best = results[0] if results else {}
    best_by_combo = {}
    for r in results:
        if r["combo"] not in best_by_combo:
            best_by_combo[r["combo"]] = r
    out = {"generated": "2026-06-16", "days": args.days, "results": results,
           "best": best, "best_by_combo": best_by_combo,
           "best_beats_intraday_only": best.get("combo") == "intraday"}
    Path("momentum_desk/edge/combos_optimize_snapshot.json").write_text(json.dumps(out, indent=2))
    print(f"\n  BEST: {best.get('combo')} exit={best.get('intraday_exit')} mc={best.get('max_concurrent')} "
          f"sharpe {best.get('daily_sharpe')}")
    print(f"  best is intraday-only: {out['best_beats_intraday_only']}")


if __name__ == "__main__":
    main()
