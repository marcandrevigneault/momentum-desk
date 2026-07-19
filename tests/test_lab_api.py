"""End-to-end smoke for the /api/lab surface: seeded strategies, run → persist →
leaderboard, active selection. Uses an in-memory Lab DB (LAB_DB=:memory:)."""
from __future__ import annotations

import os

os.environ["LAB_DB"] = ":memory:"   # set before the app's lifespan opens the store
os.environ["LAB_SEED"] = "off"      # skip the heavy committed seed in tests

from fastapi.testclient import TestClient  # noqa: E402

from momentum_desk.server import app  # noqa: E402


def test_lab_flow():
    with TestClient(app) as c:
        # seeded canonical strategies
        listing = c.get("/api/lab/strategies").json()
        names = [s["name"] for s in listing["strategies"]]
        assert "Intraday momentum" in names and any("Fade" in n for n in names)

        # run one → persisted, result returned
        run = c.post("/api/lab/run", json={"name": "Intraday momentum", "window": "1y"}).json()
        assert run["ok"] and run["run_id"] >= 1
        assert "metrics" in run["result"] and "expectancy_r" in run["result"]["metrics"]

        # leaderboard now has it
        board = c.get("/api/lab/leaderboard").json()["runs"]
        assert any(r["strategy"] == "Intraday momentum" for r in board)

        # active selection
        assert c.post("/api/lab/active", json={"name": "Intraday momentum"}).json()["ok"]
        assert c.get("/api/lab/active").json()["active"] == "Intraday momentum"

        # save a new strategy, see it listed
        c.post("/api/lab/strategies", json={"name": "my-custom", "kind": "single", "session": "premarket"})
        names2 = [s["name"] for s in c.get("/api/lab/strategies").json()["strategies"]]
        assert "my-custom" in names2


def test_lab_api_create_and_run_insider(monkeypatch):
    """kind='insider' end-to-end through the HTTP API: save an inline strategy,
    run it on the synthetic bundle, confirm the insider path was actually
    exercised (not silently dispatched elsewhere). No data key -> best_data_source()
    picks synthetic regardless of the environment running the suite."""
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    with TestClient(app) as c:
        r = c.post("/api/lab/strategies", json={
            "name": "My insider", "kind": "insider", "insider": {"roles": "ceo_cfo"},
        })
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] and body["strategy"]["kind"] == "insider"

        r = c.post("/api/lab/run", json={"name": "My insider", "window": "1y", "data_source": "synthetic"})
        assert r.status_code == 200
        run = r.json()
        assert run["ok"]
        assert run["data_source"] == "synthetic"
        assert run["result"]["metrics"]["trades"] >= 0
        assert run["result"]["config"].get("roles") == "ceo_cfo"
