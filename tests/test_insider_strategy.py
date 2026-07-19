"""Strategy/Lab integration for the insider-buying strategy: round-trip of the
``insider`` config dict on Strategy, dispatch through run_strategy into
run_insider_strategy, the synthetic bundle's fabricated-filings -> real
build_events/filter_events pipeline, and the Lab's canonical variants /
provider-factory wiring."""
from __future__ import annotations

import json

from momentum_desk.edge.lab import CANONICAL, gauntlet_key, run_only, seed
from momentum_desk.edge.result import AccountRun
from momentum_desk.edge.store import LabStore
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


def test_seed_backfills_insider_canonicals_into_nonempty_store(monkeypatch):
    """Task 6 review finding: seed() only populated CANONICAL strategies into an
    EMPTY store, so a pre-existing (non-empty) data/lab.db never got the 5 new
    insider variants — the feature stayed invisible in a running Lab. seed()
    must now backfill any missing CANONICAL name into a non-empty store, leave
    pre-existing strategies untouched, and be idempotent (no duplicates)."""
    monkeypatch.setenv("LAB_SEED", "off")
    store = LabStore(":memory:")
    pre_existing = Strategy(name="my-custom", kind="single", session="premarket")
    store.save_strategy(pre_existing)

    seed(store)

    names = {s.name for s in store.list_strategies()}
    insider_names = {s.name for s in CANONICAL if s.kind == "insider"}
    assert insider_names <= names
    assert "my-custom" in names
    got = store.get_strategy("my-custom")
    assert got == pre_existing

    # idempotent: calling again doesn't duplicate rows
    seed(store)
    names_after = [s.name for s in store.list_strategies()]
    assert len(names_after) == len(set(names_after))
    assert insider_names <= set(names_after)


def test_seed_loads_insider_canonicals_on_first_boot_with_pre_insider_seed_json(monkeypatch, tmp_path):
    """Task 6 review finding continued: on a FRESH empty store, seed() only
    populated whatever `data["strategies"]` the committed (pre-insider)
    lab_seed.json contained, and the CANONICAL backfill previously ran only
    in the non-empty-store `else` branch — so insider variants were invisible
    until a SECOND seed() call. One seed() call against an empty store, with
    a seed json missing the insider variants, must now surface them
    immediately."""
    monkeypatch.delenv("LAB_SEED", raising=False)   # exercise the real (non-test-mode) path
    seed_path = tmp_path / "lab_seed.json"
    seed_path.write_text(json.dumps({
        "strategies": [
            {"name": "Intraday momentum", "kind": "single", "session": "intraday"},
        ],
        "runs": [],
    }))
    import momentum_desk.edge.lab as lab_module
    monkeypatch.setattr(lab_module, "SEED_PATH", seed_path)

    store = LabStore(":memory:")
    seed(store)

    names = {s.name for s in store.list_strategies()}
    insider_names = {s.name for s in CANONICAL if s.kind == "insider"}
    assert "Intraday momentum" in names
    assert insider_names <= names
