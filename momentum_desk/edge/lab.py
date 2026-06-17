"""Strategy Lab orchestration — ties the Strategy object, the run dispatcher and
the SQLite store together behind a few simple calls the server exposes under
/api/lab. This is the single seam the unified UI talks to.

Runs use synthetic data (instant) for now; real-data (polygon) runs are the same
``run_strategy`` call with a polygon provider factory and belong on the existing
background-job queue — a later wiring, not a new engine.
"""
from __future__ import annotations

from ..backtest.providers import SyntheticHistory
from .store import LabStore
from .strategy import LegSpec, Strategy, run_strategy

# The strategies the Lab ships with, so the leaderboard isn't empty on first run.
# (These mirror the Simulation page's selector; the Lab is where they now live.)
CANONICAL: list[Strategy] = [
    Strategy(name="Intraday momentum", kind="single", session="intraday"),
    Strategy(name="Combo: Intraday only", kind="combo",
             legs=[LegSpec(name="intraday", session="intraday")]),
    Strategy(name="Combo: Premarket + Intraday", kind="combo",
             legs=[LegSpec(name="premarket", session="premarket"),
                   LegSpec(name="intraday", session="intraday")]),
    Strategy(name="Combo: 3-leg (+fade)", kind="combo",
             legs=[LegSpec(name="premarket", session="premarket"),
                   LegSpec(name="intraday", session="intraday"),
                   LegSpec(name="fade", session="intraday", style="fade")]),
]

_DAYS = {"1y": 252, "5y": 1260}


def seed(store: LabStore) -> None:
    """Register the canonical strategies if the store has none yet."""
    if not store.list_strategies():
        for s in CANONICAL:
            store.save_strategy(s)


def _synthetic_factory(days: int):
    return lambda session: SyntheticHistory(days=days, session=session)


def run_only(strategy: Strategy, *, window: str = "1y", account_equity: float = 25_000.0):
    """Just the compute — run a strategy on synthetic data over the window and
    return the AccountRun. Safe to call off the event loop (no DB handle here, so
    it doesn't cross sqlite's thread affinity)."""
    days = _DAYS.get(window, 252)
    return run_strategy(strategy, _synthetic_factory(days), account_equity=account_equity)


def run_and_store(store: LabStore, strategy: Strategy, *, window: str = "1y",
                  account_equity: float = 25_000.0) -> dict:
    """Compute then persist — for synchronous callers (tests). The server splits
    these so the DB write stays on the connection's thread."""
    result = run_only(strategy, window=window, account_equity=account_equity)
    run_id = store.save_run(strategy, window, "synthetic", result)
    return {"run_id": run_id, "result": result}
