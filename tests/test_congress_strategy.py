"""Strategy/Lab integration for the congress-trading strategy: round-trip of
the ``congress`` config dict on Strategy, dispatch through run_strategy into
run_congress_strategy, the synthetic bundle's fabricated-trades -> real
build_events pipeline (reusing insider's daily-bar simulator), and the Lab's
canonical variants / provider-factory wiring. Mirrors
tests/test_insider_strategy.py's structure closely."""
from __future__ import annotations

import json

import pytest

from momentum_desk.congress.bundles import SyntheticCongressBundle
from momentum_desk.congress.signals import CongressConfig
from momentum_desk.edge.lab import CANONICAL, gauntlet_key, run_only, seed
from momentum_desk.edge.result import AccountRun
from momentum_desk.edge.store import LabStore
from momentum_desk.edge.strategy import Strategy, run_strategy
from momentum_desk.risk import RiskConfig


def test_strategy_congress_round_trip():
    s = Strategy(name="cong", kind="congress",
                 congress={"power_only": True, "cluster_n": 2})
    back = Strategy.from_dict(s.to_dict())
    assert back == s
    assert back.congress == {"power_only": True, "cluster_n": 2}


def test_run_strategy_dispatches_congress():
    s = Strategy(name="cong", kind="congress")

    def provider_factory(session: str):
        assert session == "congress"
        return SyntheticCongressBundle(days=60)

    run = run_strategy(s, provider_factory)
    assert isinstance(run, AccountRun)
    assert run.trades is not None and len(run.trades) >= 0
    assert run.days > 0


def test_synthetic_bundle_produces_events():
    cfg = CongressConfig()
    a = SyntheticCongressBundle(days=252, seed=7).events(cfg)
    b = SyntheticCongressBundle(days=252, seed=7).events(cfg)
    assert len(a) > 0
    assert a == b


def test_synthetic_bundle_density_at_days_60():
    cfg = CongressConfig()
    events = SyntheticCongressBundle(days=60).events(cfg)
    assert len(events) >= 1


def test_synthetic_bundle_exposes_fake_power_set():
    bundle = SyntheticCongressBundle(days=120)
    assert isinstance(bundle.power, set) and len(bundle.power) > 0


def test_gauntlet_key_none_for_congress():
    s = Strategy(name="cong", kind="congress")
    assert gauntlet_key(s) is None


def test_canonical_contains_congress_variants():
    names = {s.name for s in CANONICAL if s.kind == "congress"}
    assert names == {
        "Congress: member buys",
        "Congress: power buys",
        "Congress: cluster buys",
    }


def test_lab_run_only_congress_synthetic(monkeypatch):
    monkeypatch.setenv("LAB_SEED", "off")
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    s = Strategy(name="cong", kind="congress")
    run = run_only(s, window="1y")
    assert isinstance(run, AccountRun)
    assert "expectancy_r" in run.metrics


def test_run_congress_strategy_wires_config_and_risk():
    from momentum_desk.congress.bundles import run_congress_strategy
    s = Strategy(name="cong", kind="congress", congress={"min_amount": 30_000.0, "bogus": 1})
    bundle = SyntheticCongressBundle(days=120)
    run = run_congress_strategy(s, bundle, RiskConfig(account_equity=10_000.0))
    assert isinstance(run, AccountRun)
    assert run.starting_equity == 10_000.0
    assert run.config["min_amount"] == 30_000.0


def test_run_congress_strategy_rejects_unknown_owner():
    from momentum_desk.congress.bundles import run_congress_strategy
    s = Strategy(name="cong", kind="congress", congress={"owners": ["SELF", "BOGUS"]})
    bundle = SyntheticCongressBundle(days=60)
    with pytest.raises(ValueError):
        run_congress_strategy(s, bundle, RiskConfig())


def test_run_congress_strategy_rejects_cluster_n_below_one():
    from momentum_desk.congress.bundles import run_congress_strategy
    s = Strategy(name="cong", kind="congress", congress={"cluster_n": 0})
    bundle = SyntheticCongressBundle(days=60)
    with pytest.raises(ValueError):
        run_congress_strategy(s, bundle, RiskConfig())


def test_seed_backfills_congress_canonicals_into_nonempty_store(monkeypatch):
    """Same review finding as insider's Task 6: seed() must backfill any
    missing CANONICAL name into a non-empty store, leave pre-existing
    strategies untouched, and be idempotent."""
    monkeypatch.setenv("LAB_SEED", "off")
    store = LabStore(":memory:")
    pre_existing = Strategy(name="my-custom", kind="single", session="premarket")
    store.save_strategy(pre_existing)

    seed(store)

    names = {s.name for s in store.list_strategies()}
    congress_names = {s.name for s in CANONICAL if s.kind == "congress"}
    assert congress_names <= names
    assert "my-custom" in names
    got = store.get_strategy("my-custom")
    assert got == pre_existing

    # idempotent: calling again doesn't duplicate rows
    seed(store)
    names_after = [s.name for s in store.list_strategies()]
    assert len(names_after) == len(set(names_after))
    assert congress_names <= set(names_after)


def test_seed_loads_congress_canonicals_on_first_boot_with_pre_congress_seed_json(monkeypatch, tmp_path):
    """Same review finding as insider's Task 6, continued: on a FRESH empty
    store, a committed lab_seed.json missing the congress variants must
    still surface them immediately on the very first seed() call."""
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
    congress_names = {s.name for s in CANONICAL if s.kind == "congress"}
    assert "Intraday momentum" in names
    assert congress_names <= names
