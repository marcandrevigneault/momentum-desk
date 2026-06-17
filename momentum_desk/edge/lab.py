"""Strategy Lab orchestration — Strategy object + run dispatcher + SQLite store
behind the /api/lab surface.

Runs use REAL data (Polygon/Massive) whenever a data key is present, else fall
back to synthetic. Because real runs are slow, the leaderboard is populated from
a committed seed of precomputed real runs (edge/lab_seed.json) on first start —
no manual "run" button, no live fetch on boot. A recompute is an explicit action.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from ..backtest.providers import PolygonHistory, SyntheticHistory
from .store import LabStore
from .strategy import LegSpec, Strategy, run_strategy

# The strategies the Lab ships with. A strategy is just one-or-more legs; the
# single-leg ones are plain strategies (no "combo" noun).
CANONICAL: list[Strategy] = [
    Strategy(name="Intraday momentum", kind="single", session="intraday"),
    Strategy(name="Premarket + Intraday", kind="combo",
             legs=[LegSpec(name="premarket", session="premarket"),
                   LegSpec(name="intraday", session="intraday")]),
    Strategy(name="Premarket + Intraday + Fade", kind="combo",
             legs=[LegSpec(name="premarket", session="premarket"),
                   LegSpec(name="intraday", session="intraday"),
                   LegSpec(name="fade", session="intraday", style="fade")]),
]

_DAYS = {"1y": 252, "5y": 1260}
SEED_PATH = Path(__file__).parent / "lab_seed.json"


def _data_key() -> str | None:
    return os.environ.get("POLYGON_API_KEY") or os.environ.get("MASSIVE_API_KEY")


def best_data_source() -> str:
    """Real data when a key is available, else synthetic (so it still runs)."""
    return "polygon" if _data_key() else "synthetic"


def _provider_factory(days: int, data_source: str):
    if data_source == "polygon":
        key = _data_key()
        return lambda session: PolygonHistory(
            api_key=key, days=days,
            universe_mode="active" if session == "intraday" else "gap", max_per_min=0)
    return lambda session: SyntheticHistory(days=days, session=session)


def run_only(strategy: Strategy, *, window: str = "1y", data_source: str | None = None,
             account_equity: float = 25_000.0):
    """Just the compute — run a strategy over the window on the chosen data
    source and return the AccountRun. Safe off the event loop (no DB handle)."""
    data_source = data_source or best_data_source()
    days = _DAYS.get(window, 252)
    return run_strategy(strategy, _provider_factory(days, data_source), account_equity=account_equity)


def seed(store: LabStore) -> None:
    """Register the seed strategies (canonical + the optimizer sweep) and, if the
    store has no runs yet, load the committed real-data runs so the leaderboard is
    populated and ranked immediately."""
    test_mode = os.environ.get("LAB_SEED", "").lower() == "off"
    data = {}
    if SEED_PATH.exists() and not test_mode:
        try:
            data = json.loads(SEED_PATH.read_text())
        except Exception:  # noqa: BLE001
            data = {}
    if not store.list_strategies():
        strats = [Strategy.from_dict(c) for c in data["strategies"]] if data.get("strategies") else CANONICAL
        for s in strats:
            store.save_strategy(s)
    if test_mode:
        return
    if not store.leaderboard():
        for run in data.get("runs", []):
            store.add_run_raw(run["strategy"], run.get("kind", "single"), run["window"],
                              run.get("data_source", "polygon"), run["result"])
