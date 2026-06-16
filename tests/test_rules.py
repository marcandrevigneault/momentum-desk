"""AND/OR rule composer: condition logic + AND vs OR behaviour."""
from __future__ import annotations

from momentum_desk.backtest.providers import SyntheticHistory
from momentum_desk.edge.optimize import build_eval_events
from momentum_desk.edge.rules import Condition, RuleSet, run_presets, run_ruleset
from momentum_desk.edge.screen import ScreenConfig


def _events():
    return build_eval_events(SyntheticHistory(days=60, session="intraday"),
                             ScreenConfig(session="intraday"), 0.3)


def test_condition_ops():
    e = build_eval_events(SyntheticHistory(days=20, session="intraday"), ScreenConfig(session="intraday"), 0.3)[0]
    assert Condition("rvol", ">", -1).test(e)        # rvol always > -1
    assert not Condition("rvol", "<", -1).test(e)


def test_and_is_stricter_than_or():
    events = _events()
    assert events, "need events"
    low_ext = Condition("ext_vwap_pct", "<", 8.0)
    rvol_cap = Condition("rvol", "<", 10.0)
    n_and = run_ruleset(events, RuleSet("and", [low_ext, rvol_cap], "AND")).n
    n_or = run_ruleset(events, RuleSet("or", [low_ext, rvol_cap], "OR")).n
    n_all = run_ruleset(events, RuleSet("all", [], "AND")).n
    assert n_and <= n_or <= n_all   # AND ⊆ OR ⊆ all entries


def test_run_presets_ranks_by_sharpe():
    res = run_presets(SyntheticHistory(days=90, session="intraday"), ScreenConfig(session="intraday"))
    assert len(res) >= 5
    sh = [r.daily_sharpe for r in res]
    assert sh == sorted(sh, reverse=True)
