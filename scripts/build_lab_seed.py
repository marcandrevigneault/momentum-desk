"""Compute the canonical Lab strategies on REAL data → edge/lab_seed.json, so the
deployed leaderboard shows real Polygon numbers with no live fetch on boot.

Each run is computed in its OWN subprocess so memory (multi-year Polygon minute
caches, esp. 3-leg 5y combos) is fully reclaimed between runs — one process never
holds more than a single run's providers.

    POLYGON_API_KEY=... python -m scripts.build_lab_seed

The committed seed is slimmed: trade log capped to the most-recent, equity curve
downsampled. Metrics + monthly rollup stay full.
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from momentum_desk.edge.lab import CANONICAL, best_data_source, run_only

_CURVE_PTS = 400        # equity-curve points kept (downsampled)


def _slim(d: dict) -> dict:
    # keep the FULL trade log (so month→trades drill-down works for every month);
    # only the equity curve is downsampled for size.
    curve = d.get("equity_curve", [])
    if len(curve) > _CURVE_PTS:
        step = len(curve) / _CURVE_PTS
        d["equity_curve"] = [curve[int(i * step)] for i in range(_CURVE_PTS)] + [curve[-1]]
    return d


def _compute_one(idx: int, window: str, ds: str) -> dict:
    strat = CANONICAL[idx]
    result = run_only(strat, window=window, data_source=ds)
    return {"strategy": strat.name, "kind": strat.kind, "window": window,
            "data_source": ds, "result": _slim(asdict(result))}


def main() -> None:
    # worker mode: compute ONE run, write it to a temp file, exit (frees memory)
    if len(sys.argv) >= 5 and sys.argv[1] == "--one":
        idx, window, ds = int(sys.argv[2]), sys.argv[3], sys.argv[4]
        Path(sys.argv[5]).write_text(json.dumps(_compute_one(idx, window, ds)))
        return

    ds = best_data_source()
    print(f"data source: {ds}", flush=True)
    runs = []
    for idx in range(len(CANONICAL)):
        for window in ("1y", "5y"):
            part = f"/tmp/lab_seed_part_{idx}_{window}.json"
            subprocess.run([sys.executable, "-u", "-m", "scripts.build_lab_seed",
                            "--one", str(idx), window, ds, part], check=True)
            run = json.loads(Path(part).read_text())
            Path(part).unlink(missing_ok=True)
            runs.append(run)
            m = run["result"]["metrics"]
            print(f"  {run['strategy']:30} {window}  final ${run['result']['final_equity']:>11,.0f}  "
                  f"expR {m['expectancy_r']:+.3f}  PF {m['profit_factor']:.2f}  trades {m['trades']}", flush=True)
    out = Path("momentum_desk/edge/lab_seed.json")
    out.write_text(json.dumps({"runs": runs}))
    print(f"  -> wrote {out} ({out.stat().st_size / 1e6:.1f}MB, {len(runs)} runs, source={ds})", flush=True)


if __name__ == "__main__":
    main()
