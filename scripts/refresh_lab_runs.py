"""Re-run every stored Lab strategy on the 1y and 5y windows ending today, on
real Polygon data (disk-cached under data/cache/polygon), and save each run to
the store — so the leaderboard reflects the market up to now.

    POLYGON_API_KEY=... python -m scripts.refresh_lab_runs
    POLYGON_API_KEY=... python -m scripts.refresh_lab_runs --windows 1y

Refuses to run without a data key: the leaderboard holds real-data runs, and
silently replacing them with synthetic ones would poison the ranking.
"""
from __future__ import annotations

import argparse
import os
import time
import traceback

from momentum_desk.edge.lab import best_data_source, run_only, seed
from momentum_desk.edge.store import LabStore


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", default="1y,5y", help="comma-separated: 1y,5y")
    ap.add_argument("--db", default=os.environ.get("LAB_DB", "data/lab.db"))
    ap.add_argument("--only", default=None, help="run just this strategy name")
    args = ap.parse_args()

    if best_data_source() != "polygon":
        raise SystemExit("no POLYGON_API_KEY/MASSIVE_API_KEY — refusing to refresh "
                         "the real-data leaderboard with synthetic runs")

    store = LabStore(args.db)
    seed(store)
    strategies = store.list_strategies()
    if args.only:
        strategies = [s for s in strategies if s.name == args.only]
    windows = [w.strip() for w in args.windows.split(",") if w.strip()]

    total = len(strategies) * len(windows)
    done = 0
    print(f"[refresh] {len(strategies)} strategies x {windows} -> {total} runs "
          f"(db={args.db})", flush=True)
    t0 = time.time()
    for window in windows:              # all 1y first: fast feedback, then the 5y grind
        for strat in strategies:
            done += 1
            tag = f"[{done}/{total}] {strat.name} · {window}"
            try:
                t1 = time.time()
                result = run_only(strat, window=window, data_source="polygon")
                run_id = store.save_run(strat, window, "polygon", result)
                m = result.metrics or {}
                print(f"{tag}: run #{run_id} in {time.time() - t1:.0f}s — "
                      f"expR {m.get('expectancy_r', 0):+.3f} · PF {m.get('profit_factor', 0):.2f} · "
                      f"final ${result.final_equity:,.0f}", flush=True)
            except Exception as e:  # noqa: BLE001 — one bad strategy must not kill the sweep
                print(f"{tag}: FAILED — {e}", flush=True)
                traceback.print_exc()
    print(f"[refresh] done in {(time.time() - t0) / 60:.1f} min", flush=True)


if __name__ == "__main__":
    main()
