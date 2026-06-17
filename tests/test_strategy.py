"""The Strategy keystone: serialization round-trip + the unified run dispatcher
(single and combo both return the shared AccountRun)."""
from __future__ import annotations

from momentum_desk.backtest.providers import SyntheticHistory
from momentum_desk.edge.result import AccountRun
from momentum_desk.edge.strategy import LegSpec, SizingSpec, Strategy, run_strategy


def _provider_factory(session: str):
    return SyntheticHistory(days=40, session=session)


def test_to_from_dict_round_trips():
    s = Strategy(name="intraday", session="intraday", sizing=SizingSpec(mode="compound", risk_pct=1.0),
                 kind="combo", legs=[LegSpec(name="intraday", session="intraday"),
                                     LegSpec(name="fade", session="intraday", style="fade")])
    back = Strategy.from_dict(s.to_dict())
    assert back == s
    assert back.sizing.mode == "compound" and len(back.legs) == 2 and back.legs[1].style == "fade"


def test_from_dict_ignores_unknown_keys():
    s = Strategy.from_dict({"name": "x", "session": "intraday", "bogus": 1,
                            "sizing": {"mode": "fixed", "risk_pct": 2.0, "junk": 9}})
    assert s.name == "x" and s.sizing.risk_pct == 2.0


def test_run_single_strategy_returns_account_run():
    s = Strategy(name="intraday", kind="single", session="intraday")
    run = run_strategy(s, _provider_factory)
    assert isinstance(run, AccountRun)
    assert run.days == 40 and run.starting_equity == 25_000.0
    assert "expectancy_r" in run.metrics


def test_run_combo_strategy_returns_account_run_with_legs():
    s = Strategy(name="two-leg", kind="combo",
                 legs=[LegSpec(name="intraday", session="intraday"),
                       LegSpec(name="premarket", session="premarket")])
    run = run_strategy(s, _provider_factory)
    assert isinstance(run, AccountRun)
    # ComboResult extends AccountRun with leg attribution
    assert set(run.legs) == {"intraday", "premarket"}      # type: ignore[attr-defined]
    assert run.leg_pnl and run.metrics                     # type: ignore[attr-defined]


def test_compound_sizing_grows_more_than_fixed():
    """Same strategy, fixed vs compound sizing — compound ends with more equity
    on a profitable synthetic feed (sizes off the growing book)."""
    base = Strategy(name="s", kind="single", session="intraday")
    fixed = run_strategy(base, _provider_factory)
    base.sizing = SizingSpec(mode="compound", risk_pct=1.0)
    comp = run_strategy(base, _provider_factory)
    assert comp.final_equity > fixed.final_equity      # type: ignore[attr-defined]
