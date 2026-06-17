"""Compute the Lab strategies (canonical + an optimizer SWEEP) on REAL data →
edge/lab_seed.json, so the deployed leaderboard is rich and ranked with no live
fetch on boot.

Each run is its own subprocess (memory-safe), and runs already present in the
existing seed are REUSED (so the expensive canonical 5y combos aren't recomputed
every time). Seed format: {"strategies": [config...], "runs": [run...]}.

    POLYGON_API_KEY=... python -m scripts.build_lab_seed
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from momentum_desk.edge.lab import CANONICAL, SEED_PATH, best_data_source, run_only
from momentum_desk.edge.strategy import LegSpec, SizingSpec, Strategy

_CURVE_PTS = 400

# --- the sweep: variants ranked side-by-side on the leaderboard (1y) ----------
_SWEEP_EXITS = ["time_only", "fixed_2r", "fixed_3r", "pct_trail_10",
                "atr_trail_2", "atr_trail_3", "structure_trail", "vwap_loss"]


def _sweep() -> list[Strategy]:
    out: list[Strategy] = []
    # single intraday across exit policy × sizing
    for ex in _SWEEP_EXITS:
        for mode, risk in (("fixed", 1.0), ("compound", 1.0)):
            out.append(Strategy(name=f"Intraday · {ex} · {('cmp' if mode == 'compound' else 'fix')} {risk:g}%",
                                kind="single", session="intraday", exit_policy=ex,
                                sizing=SizingSpec(mode=mode, risk_pct=risk)))
    # combos across a couple of alternative exits (the default pct_trail_10 is canonical)
    for ex in ("fixed_3r", "atr_trail_2"):
        out.append(Strategy(name=f"Premkt+Intraday · {ex}", kind="combo", exit_policy=ex,
                            legs=[LegSpec("premarket", "premarket", exit_policy=ex),
                                  LegSpec("intraday", "intraday", exit_policy=ex)]))
        out.append(Strategy(name=f"3-leg · {ex}", kind="combo", exit_policy=ex,
                            legs=[LegSpec("premarket", "premarket", exit_policy=ex),
                                  LegSpec("intraday", "intraday", exit_policy=ex),
                                  LegSpec("fade", "intraday", style="fade", exit_policy=ex)]))
    return out


def _windows(strat: Strategy) -> tuple[str, ...]:
    return ("1y", "5y") if strat in CANONICAL else ("1y",)   # sweep is 1y only (fast)


def _slim(d: dict) -> dict:
    curve = d.get("equity_curve", [])
    if len(curve) > _CURVE_PTS:
        step = len(curve) / _CURVE_PTS
        d["equity_curve"] = [curve[int(i * step)] for i in range(_CURVE_PTS)] + [curve[-1]]
    return d


def main() -> None:
    if len(sys.argv) >= 5 and sys.argv[1] == "--one":
        # worker: rebuild the strategy from its JSON config, compute one window
        cfg = json.loads(sys.argv[2])
        window, ds, out = sys.argv[3], sys.argv[4], sys.argv[5]
        result = run_only(Strategy.from_dict(cfg), window=window, data_source=ds)
        Path(out).write_text(json.dumps(_slim(asdict(result))))
        return

    ds = best_data_source()
    print(f"data source: {ds}", flush=True)
    strategies = list(CANONICAL) + _sweep()
    # reuse runs already in the existing seed (avoid recomputing canonical 5y)
    existing: dict[tuple, dict] = {}
    if SEED_PATH.exists():
        for r in json.loads(SEED_PATH.read_text()).get("runs", []):
            existing[(r["strategy"], r["window"])] = r

    runs = []
    for strat in strategies:
        for window in _windows(strat):
            key = (strat.name, window)
            if key in existing:
                runs.append(existing[key])
                print(f"  reuse  {strat.name:34} {window}", flush=True)
                continue
            part = f"/tmp/seed_{abs(hash(key))}.json"
            subprocess.run([sys.executable, "-u", "-m", "scripts.build_lab_seed", "--one",
                            json.dumps(strat.to_dict()), window, ds, part], check=True)
            res = json.loads(Path(part).read_text())
            Path(part).unlink(missing_ok=True)
            runs.append({"strategy": strat.name, "kind": strat.kind, "window": window,
                         "data_source": ds, "result": res})
            print(f"  run    {strat.name:34} {window}  final ${res['final_equity']:>11,.0f}  "
                  f"expR {res['metrics']['expectancy_r']:+.3f}", flush=True)

    SEED_PATH.write_text(json.dumps({"strategies": [s.to_dict() for s in strategies], "runs": runs}))
    print(f"  -> wrote {SEED_PATH} ({SEED_PATH.stat().st_size / 1e6:.1f}MB, "
          f"{len(strategies)} strategies, {len(runs)} runs)", flush=True)


if __name__ == "__main__":
    main()
