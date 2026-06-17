"""The live observability surface: /api/live/bars and /api/live/intent. The
engine is opt-in, so by default intent reports unavailable; once a single-leg
strategy is attached it reports the (empty, nothing-transmitted) intent view."""
from __future__ import annotations

import os

os.environ["LAB_DB"] = ":memory:"
os.environ["LAB_SEED"] = "off"

from fastapi.testclient import TestClient  # noqa: E402

from momentum_desk.edge.strategy import SizingSpec, Strategy  # noqa: E402
from momentum_desk.server import app  # noqa: E402


def test_live_bars_index_empty_by_default():
    with TestClient(app) as c:
        j = c.get("/api/live/bars").json()
        assert "feed" in j and isinstance(j["symbols"], dict)


def test_live_intent_unavailable_until_attached():
    with TestClient(app) as c:
        j = c.get("/api/live/intent").json()
        assert j["available"] is False and j["armed"] is False
        assert "reason" in j


def test_live_intent_available_when_strategy_attached():
    with TestClient(app) as c:
        strat = Strategy(name="Live test", kind="single", session="intraday",
                         exit_policy="pct_trail_10", sizing=SizingSpec(mode="fixed", risk_pct=1.0))
        app.state.service.set_strategy(strat)
        j = c.get("/api/live/intent").json()
        assert j["available"] is True
        assert j["armed"] is False              # C2 never transmits
        assert j["strategy"] == "Live test"
        assert j["watching"] == [] and j["closed"] == []
        assert j["day_pnl"] == 0.0
