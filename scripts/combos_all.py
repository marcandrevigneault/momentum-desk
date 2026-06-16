"""Run a set of NAMED combos (with trades) so the dashboard can offer a combo
selector + a full trade log. Writes momentum_desk/edge/combos_snapshot.json:
{ name: {label, legs, metrics, leg_pnl, leg_trades, daily_equity, monthly, trades} }.

    python -m scripts.combos_all --data synthetic
    POLYGON_API_KEY=... python -m scripts.combos_all --data polygon --days 252
"""
from __future__ import annotations

import argparse
import gc
import json
import os
from dataclasses import asdict
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", choices=["synthetic", "polygon"], default="synthetic")
    ap.add_argument("--days", type=int, default=252)
    ap.add_argument("--out", default="momentum_desk/edge/combos_snapshot.json")
    args = ap.parse_args()

    def intraday():
        return ComboLeg(name="intraday", provider=_prov(args.data, "intraday", args.days),
                        session="intraday", exit_policy="pct_trail_10", rvol_max=20.0)

    def premarket():
        return ComboLeg(name="premarket", provider=_prov(args.data, "premarket", args.days),
                        session="premarket", exit_policy="pct_trail_10")

    def fade():
        return ComboLeg(name="fade", provider=_prov(args.data, "intraday", args.days),
                        session="intraday", style="fade", exit_policy="pct_trail_10", slippage_pct=0.5)

    # NOTE: legs are built lazily per-combo (factories, not prebuilt instances).
    # Each provider caches every symbol-day's minute bars in RAM for the whole
    # window; holding all six legs at once OOMs on a 5y (1260-day) run. Building
    # one combo's legs at a time and freeing them keeps peak memory to the single
    # heaviest combo (3 legs) instead of all six.
    combos = {
        "intraday": ("Intraday only", lambda: [intraday()]),
        "premkt_intraday": ("Premarket + Intraday (no fade)", lambda: [premarket(), intraday()]),
        "three_leg": ("Premarket + Intraday + Fade", lambda: [premarket(), intraday(), fade()]),
    }

    # each combo is emitted twice: fixed-dollar risk (key as-is) and compounding
    # risk on the live book (key + "_c", label + " (% equity)"). Both variants
    # reuse the same (already-cached) legs, so compounding costs compute, not RAM.
    variants = [("", False, ""), ("_c", True, " (% equity)")]

    out = {"generated": "2026-06-16", "days": args.days, "data": args.data, "combos": {}}
    for key, (label, make_legs) in combos.items():
        legs = make_legs()
        for kx, compound, lx in variants:
            res = run_combo(legs, ComboConfig(),
                            RiskConfig(account_equity=25_000, compound=compound))
            d = asdict(res)
            d.pop("equity_curve", None)   # unused by the page; slim
            d["label"] = label + lx
            out["combos"][key + kx] = d
            m = res.metrics
            print(f"  {label + lx:<44} ${res.final_equity:>10,.0f}  PF {m['profit_factor']:>5.2f}  "
                  f"expR {m['expectancy_r']:>+6.3f}  maxDD {m['max_drawdown_pct']:>5.1f}%  legs {res.leg_pnl}")
        del legs                    # release this combo's minute caches before the next
        gc.collect()

    path = Path(args.out)
    path.write_text(json.dumps(out))
    print(f"  → wrote {path} ({path.stat().st_size/1e6:.1f}MB)")


if __name__ == "__main__":
    main()
