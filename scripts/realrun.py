"""Multi-year real-data pre-market backtest: parameter sweep (incl. the
time-of-day exit) + walk-forward, on Massive history. Fetches once into the disk
cache, then every combo replays for free.

    POLYGON_API_KEY=... RUN_DAYS=504 python scripts/realrun.py

Writes data/realrun_results.txt (human summary) and data/realrun.json (full
trades + equity + month/year breakdowns + sweep + walk-forward) for the
visualizer's "Load real run". Both gitignored.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, replace

from momentum_desk.backtest import Backtester, PolygonHistory, sweep, walk_forward
from momentum_desk.backtest.engine import BacktestConfig
from momentum_desk.backtest.review import breakdowns
from momentum_desk.scanner import ScanConfig

KEY = os.environ["POLYGON_API_KEY"]
DAYS = int(os.environ.get("RUN_DAYS", "504"))   # ~2 years of weekdays

prov = PolygonHistory(KEY, days=DAYS, max_per_min=0, max_candidates_per_day=8,
                      fetch_news=False, cache_dir="data/cache/polygon")
scan = ScanConfig(require_news=False, min_relative_volume=3.0)
base = BacktestConfig(session="premarket")

t0 = time.time()
os.makedirs("data", exist_ok=True)
lines: list[str] = []
try:
    rows = sweep(prov, scan=scan, base=base)
    wf = walk_forward(prov, scan=scan, base=base, folds=5)
    best = rows[0]
    res = Backtester(prov, scan=scan, bt=replace(base, **best.params)).run()
    bd = breakdowns(res.trades)

    # full structured result for the UI
    with open("data/realrun.json", "w") as fh:
        json.dump({
            "synthetic": False, "session": "premarket", "days": res.days,
            "best_params": best.params, "metrics": asdict(res.metrics),
            "equity_curve": res.equity_curve, "trades": [asdict(t) for t in res.trades],
            "monthly": bd["monthly"], "yearly": bd["yearly"],
            "sweep": [asdict(r) for r in rows], "walk_forward": asdict(wf),
        }, fh)

    lines.append(f"REAL Massive data · PRE-MARKET · {DAYS} weekdays (~{DAYS/252:.1f}y) · "
                 f"{time.time()-t0:.0f}s · calls {prov.client.calls}, hits {prov.client.cache_hits}")
    lines.append("\nsweep — rank by R/trade:")
    hdr = f"  {'target_r':>9}{'stop%':>7}{'exit@':>8}{'trades':>8}"
    lines.append(hdr + f"{'win%':>7}{'PF':>8}{'R/trade':>9}{'maxDD%':>8}")
    for r in rows:
        cap = r.params.get("time_exit_tod", 0)
        caps = "none" if not cap else f"{cap // 60:02d}:{cap % 60:02d}"
        lines.append(f"  {r.params['target_r']:>9}{r.params['stop_buffer_pct']:>7}{caps:>8}"
                     f"{r.trades:>8}{r.win_rate:>7.1f}{r.profit_factor:>8.2f}{r.expectancy_r:>9.3f}{r.max_drawdown_pct:>8.1f}")
    lines.append("\nwalk-forward (optimise on past, score on unseen future):")
    for f in wf.folds:
        lines.append(f"  train {f.train_days:>4}  test {f.test_days:>4}  IS R {f.in_sample_r:>7.3f}  "
                     f"OOS R {f.out_sample_r:>7.3f}  ({f.out_sample_trades} tr)")
    lines.append(f"  mean IS R {wf.mean_in_sample_r:+.3f} · mean OOS R {wf.mean_out_sample_r:+.3f} "
                 f"· degradation {wf.degradation:+.3f}")
    lines.append("\nyearly:")
    for y in bd["yearly"]:
        lines.append(f"  {y['period']}: {y['trades']} tr, win {y['win_rate']}%, pnl ${y['pnl']:,.0f}")
    lines.append("VERDICT: " + ("OOS POSITIVE — worth paper-forward-testing"
                                if wf.mean_out_sample_r > 0 else
                                "OOS NEGATIVE — edge does not survive out-of-sample"))
except Exception as e:  # noqa: BLE001
    lines.append(f"RUN FAILED after {time.time()-t0:.0f}s: {e!r}")

report = "\n".join(lines)
with open("data/realrun_results.txt", "w") as fh:
    fh.write(report + "\n")
print(report)
