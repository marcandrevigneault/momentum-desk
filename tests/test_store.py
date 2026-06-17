"""The Lab SQLite store: strategies CRUD, run persistence, ranked leaderboard,
active selection. Uses an in-memory DB so nothing touches disk."""
from __future__ import annotations

import pytest

from momentum_desk.edge.portfolio import SimResult
from momentum_desk.edge.store import LabStore
from momentum_desk.edge.strategy import SizingSpec, Strategy


@pytest.fixture()
def store():
    s = LabStore(":memory:")
    yield s
    s.close()


def _run(final_equity: float, expectancy_r: float, dd: float = 5.0) -> SimResult:
    return SimResult(session="intraday", exit_policy="pct_trail_10", days=252,
                     starting_equity=25_000.0, final_equity=final_equity,
                     metrics={"expectancy_r": expectancy_r, "max_drawdown_pct": dd, "profit_factor": 2.0})


def test_strategy_crud_round_trips(store):
    s = Strategy(name="intraday", session="intraday", sizing=SizingSpec(mode="compound"))
    store.save_strategy(s)
    assert store.get_strategy("intraday") == s
    assert [x.name for x in store.list_strategies()] == ["intraday"]
    # upsert (no duplicate)
    s.exit_policy = "fixed_3r"
    store.save_strategy(s)
    assert store.get_strategy("intraday").exit_policy == "fixed_3r"
    assert len(store.list_strategies()) == 1
    store.delete_strategy("intraday")
    assert store.get_strategy("intraday") is None


def test_run_persist_and_fetch(store):
    s = Strategy(name="x")
    rid = store.save_run(s, window="1y", data_source="synthetic", result=_run(40_000, 1.5))
    got = store.get_run(rid)
    assert got["strategy"] == "x" and got["window"] == "1y"
    assert got["metrics"]["expectancy_r"] == 1.5
    assert got["result"]["final_equity"] == 40_000


def test_leaderboard_ranks_by_metric(store):
    a, b, c = Strategy(name="a"), Strategy(name="b"), Strategy(name="c")
    store.save_run(a, "1y", "synthetic", _run(30_000, 0.5, dd=2.0))
    store.save_run(b, "1y", "synthetic", _run(90_000, 2.5, dd=9.0))
    store.save_run(c, "1y", "synthetic", _run(60_000, 1.2, dd=4.0))
    # default rank = expectancy_r descending
    board = store.leaderboard()
    assert [r["strategy"] for r in board] == ["b", "c", "a"]
    # drawdown ranks ascending (lower is better)
    by_dd = store.leaderboard(rank_by="max_drawdown_pct")
    assert [r["strategy"] for r in by_dd] == ["a", "c", "b"]
    # unknown metric falls back to the default (no SQL injection surface)
    assert store.leaderboard(rank_by="oops; DROP TABLE runs")[0]["strategy"] == "b"


def test_active_selection(store):
    assert store.get_active() is None
    store.set_active("intraday")
    assert store.get_active() == "intraday"
    store.set_active("combo-three")
    assert store.get_active() == "combo-three"
