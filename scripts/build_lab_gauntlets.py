"""Precompute the evaluation Gauntlet (bootstrap CI, deflated Sharpe, walk-forward,
regime, holdout) for each distinct single-strategy entry on REAL data → into the
Lab seed, so the per-strategy "does this survive?" panel is instant on the deploy.

Keyed by "session|exit_policy"; the multi-leg combos don't get a gauntlet (it
evaluates a single entry/exit stream). One subprocess per gauntlet (memory-safe);
already-computed keys are reused.

    POLYGON_API_KEY=... python -m scripts.build_lab_gauntlets
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from momentum_desk.edge.gauntlet import run_gauntlet
from momentum_desk.edge.lab import SEED_PATH, best_data_source
from momentum_desk.edge.screen import ScreenConfig
from momentum_desk.edge.store import LabStore
from momentum_desk.edge.strategy import Strategy

_DAYS = 252  # gauntlet on the 1y window (the sweep is 1y)


def _intraday_provider(ds: str):
    from momentum_desk.backtest.providers import PolygonHistory, SyntheticHistory
    if ds == "polygon":
        import os
        key = os.environ.get("POLYGON_API_KEY") or os.environ.get("MASSIVE_API_KEY")
        return PolygonHistory(api_key=key, days=_DAYS, universe_mode="active", max_per_min=0)
    return SyntheticHistory(days=_DAYS, session="intraday")


def _keys() -> list[tuple[str, str]]:
    """Distinct (session, exit) among the seed's SINGLE strategies."""
    out: set[tuple[str, str]] = set()
    for c in json.loads(SEED_PATH.read_text()).get("strategies", []):
        s = Strategy.from_dict(c)
        if s.kind == "single":
            out.add((s.session, s.exit_policy))
    return sorted(out)


def main() -> None:
    ds = best_data_source()
    if len(sys.argv) >= 4 and sys.argv[1] == "--one":
        session, exit_policy, out = sys.argv[2], sys.argv[3], sys.argv[4]
        res = run_gauntlet(_intraday_provider(ds), ScreenConfig(session=session),
                           candidate_policy_name=exit_policy)
        Path(out).write_text(json.dumps(asdict(res)))
        return

    # reuse anything already cached in the store's seeded gauntlets file
    store = LabStore(":memory:")
    existing = {}
    gpath = SEED_PATH.parent / "lab_gauntlets.json"
    if gpath.exists():
        existing = json.loads(gpath.read_text()).get("gauntlets", {})

    gauntlets = dict(existing)
    for session, exit_policy in _keys():
        key = f"{session}|{exit_policy}"
        if key in gauntlets:
            print(f"  reuse  {key}", flush=True)
            continue
        part = f"/tmp/gauntlet_{abs(hash(key))}.json"
        subprocess.run([sys.executable, "-u", "-m", "scripts.build_lab_gauntlets",
                        "--one", session, exit_policy, part], check=True)
        g = json.loads(Path(part).read_text())
        Path(part).unlink(missing_ok=True)
        gauntlets[key] = g
        print(f"  gauntlet {key:28} verdict={g['verdict']:6} dsr={g['dsr']:.0%} "
              f"WF_oos={g['wf_oos_exp']:+.3f} p(edge>0)={g['boot_p_pos']:.0%}", flush=True)
    store.close()
    gpath.write_text(json.dumps({"gauntlets": gauntlets}))
    print(f"  -> wrote {gpath} ({gpath.stat().st_size / 1e3:.0f}KB, {len(gauntlets)} gauntlets, {ds})", flush=True)


if __name__ == "__main__":
    main()
