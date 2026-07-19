"""Strategy/Lab integration for the insider-buying strategy: round-trip of the
``insider`` config dict on Strategy, dispatch through run_strategy into
run_insider_strategy, the synthetic bundle's fabricated-filings -> real
build_events/filter_events pipeline, and the Lab's canonical variants /
provider-factory wiring."""
from __future__ import annotations

from momentum_desk.edge.lab import CANONICAL, gauntlet_key, run_only
from momentum_desk.edge.result import AccountRun
from momentum_desk.edge.strategy import Strategy, run_strategy
from momentum_desk.insider.bundles import SyntheticInsiderBundle
from momentum_desk.insider.models import InsiderConfig
from momentum_desk.risk import RiskConfig


def test_strategy_insider_round_trip():
    s = Strategy(name="ins", kind="insider",
                 insider={"roles": "ceo_cfo", "min_value": 50_000.0})
    back = Strategy.from_dict(s.to_dict())
    assert back == s
    assert back.insider == {"roles": "ceo_cfo", "min_value": 50_000.0}


def test_run_strategy_dispatches_insider():
    s = Strategy(name="ins", kind="insider")

    def provider_factory(session: str):
        assert session == "insider"
        return SyntheticInsiderBundle(days=60)

    run = run_strategy(s, provider_factory)
    assert isinstance(run, AccountRun)
    assert run.trades is not None and len(run.trades) >= 0
    assert run.days > 0


def test_synthetic_bundle_produces_events():
    cfg = InsiderConfig()
    a = SyntheticInsiderBundle(days=252, seed=7).events(cfg)
    b = SyntheticInsiderBundle(days=252, seed=7).events(cfg)
    assert len(a) > 0
    assert a == b


def test_gauntlet_key_none_for_insider():
    s = Strategy(name="ins", kind="insider")
    assert gauntlet_key(s) is None


def test_canonical_contains_insider_variants():
    names = {s.name for s in CANONICAL if s.kind == "insider"}
    assert names == {
        "Insider: officer buys",
        "Insider: CEO/CFO buys",
        "Insider: cluster buys",
        "Insider: small-cap cluster",
        "Insider: news-quiet buys",
    }


def test_lab_run_only_insider_synthetic(monkeypatch):
    monkeypatch.setenv("LAB_SEED", "off")
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    s = Strategy(name="ins", kind="insider")
    run = run_only(s, window="1y")
    assert isinstance(run, AccountRun)
    assert "expectancy_r" in run.metrics


def test_run_insider_strategy_wires_config_and_risk():
    from momentum_desk.insider.bundles import run_insider_strategy
    s = Strategy(name="ins", kind="insider", insider={"min_value": 30_000.0, "bogus": 1})
    bundle = SyntheticInsiderBundle(days=120)
    run = run_insider_strategy(s, bundle, RiskConfig(account_equity=10_000.0))
    assert isinstance(run, AccountRun)
    assert run.starting_equity == 10_000.0
    assert run.config["min_value"] == 30_000.0
