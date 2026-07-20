"""Tests for the kadoa congress-trading loader and SQLite store.

Fixtures are inline dicts matching the real kadoa per-filer JSON shape
(confirmed via a one-off real fetch, see task report): a top-level
``{"filer": {...}, "trades": [...]}`` object. House-chamber filers use
abbreviated codes (owner "SP"/"JT", asset_type "ST"/"OP"); senate-chamber
filers use full words (owner "Joint"/"Self"/"Spouse", asset_type
"Stock"/"Other") -- both shapes are exercised here since real data mixes
them. The one real-filer/real-index check is done manually (see task
report), not here.
"""
from __future__ import annotations

import json
import logging

from momentum_desk.congress.loader import (
    CONGRESS_UA,
    FILER_INDEX_URL,
    CongressStore,
    CongressTrade,
    parse_filer_json,
)

HOUSE_FILER = {
    "id": "house_nancy_pelosi",
    "full_name": "Nancy Pelosi",
    "branch": "congress",
    "chamber": "house",
}

SENATE_FILER = {
    "id": "senate_thomasr_tillis",
    "full_name": "Thomas R Tillis",
    "branch": "congress",
    "chamber": "senate",
}


def _filer_json(filer: dict, trades: list[dict]) -> bytes:
    return json.dumps({"filer": filer, "trades": trades}).encode("utf-8")


def _index_bytes(names: list[str]) -> bytes:
    return json.dumps([{"name": n} for n in names]).encode("utf-8")


# --- parse_filer_json ------------------------------------------------


def test_parse_house_filer_abbreviated_codes():
    raw = _filer_json(
        HOUSE_FILER,
        [
            {
                "filer_id": "house_nancy_pelosi",
                "transaction_date": "2026-05-29",
                "filing_date": "2026-06-23",
                "owner": "SP",
                "ticker": "INTC",
                "asset_type": "OP",
                "transaction_type": "Purchase",
                "amount_range_low": 1000001,
                "amount_range_high": 5000000,
                "is_late": 0,
                "days_to_file": 25,
            }
        ],
    )
    trades = parse_filer_json(raw)
    assert len(trades) == 1
    t = trades[0]
    assert isinstance(t, CongressTrade)
    assert t.filer_id == "house_nancy_pelosi"
    assert t.member_name == "Nancy Pelosi"
    assert t.chamber == "house"
    assert t.ticker == "INTC"
    assert t.transaction_date == "2026-05-29"
    assert t.filing_date == "2026-06-23"
    assert t.owner == "SP"
    assert t.asset_type == "OP"
    assert t.transaction_type == "Purchase"
    assert t.amount_low == 1000001
    assert t.amount_high == 5000000
    assert t.is_late is False
    assert t.days_to_file == 25


def test_parse_senate_filer_full_word_codes_normalized():
    raw = _filer_json(
        SENATE_FILER,
        [
            {
                "filer_id": "senate_thomasr_tillis",
                "transaction_date": "2024-03-01",
                "filing_date": "2024-03-15",
                "owner": "Joint",
                "ticker": "AAPL",
                "asset_type": "Stock",
                "transaction_type": "Sale (Full)",
                "amount_range_low": 15001,
                "amount_range_high": 50000,
                "is_late": 1,
                "days_to_file": 14,
            }
        ],
    )
    trades = parse_filer_json(raw)
    assert len(trades) == 1
    t = trades[0]
    assert t.chamber == "senate"
    assert t.owner == "JT"  # normalized from the full word "Joint"
    assert t.asset_type == "Stock"  # raw code, passed through verbatim
    assert t.transaction_type == "Sale (Full)"
    assert t.is_late is True


def test_chamber_from_filer_id_prefix_other_for_non_congress():
    raw = _filer_json(
        {"id": "oge_antony_blinken", "full_name": "Antony J Blinken"},
        [
            {
                "filer_id": "oge_antony_blinken",
                "transaction_date": "2023-01-01",
                "filing_date": "2023-01-10",
                "ticker": "MSFT",
                "transaction_type": "Sale (Full)",
            }
        ],
    )
    trades = parse_filer_json(raw)
    assert trades[0].chamber == "other"


def test_tolerant_defaults_missing_owner_and_amount():
    raw = _filer_json(
        HOUSE_FILER,
        [
            {
                "filer_id": "house_nancy_pelosi",
                "transaction_date": "2024-01-01",
                "filing_date": "2024-01-10",
                "ticker": "MSFT",
                "transaction_type": "purchase",
            }
        ],
    )
    trades = parse_filer_json(raw)
    assert len(trades) == 1
    t = trades[0]
    assert t.owner == "SELF"
    assert t.amount_low == 0.0
    assert t.amount_high == 0.0
    assert t.is_late is False
    assert t.days_to_file == 0
    assert t.transaction_type == "Purchase"  # case-normalized


def test_dependent_child_owner_normalized_to_dc():
    raw = _filer_json(
        HOUSE_FILER,
        [
            {
                "filer_id": "house_nancy_pelosi",
                "transaction_date": "2024-01-01",
                "filing_date": "2024-01-10",
                "ticker": "MSFT",
                "owner": "Child",
            }
        ],
    )
    assert parse_filer_json(raw)[0].owner == "DC"


def test_bad_date_rows_skipped():
    raw = _filer_json(
        HOUSE_FILER,
        [
            {
                "filer_id": "house_nancy_pelosi",
                "transaction_date": "not-a-date",
                "filing_date": "2024-01-10",
                "ticker": "MSFT",
            },
            {
                "filer_id": "house_nancy_pelosi",
                "transaction_date": "2024-01-01",
                "filing_date": "",
                "ticker": "MSFT",
            },
        ],
    )
    assert parse_filer_json(raw) == []


def test_bad_ticker_rows_skipped():
    raw = _filer_json(
        HOUSE_FILER,
        [
            {
                "filer_id": "house_nancy_pelosi",
                "transaction_date": "2024-01-01",
                "filing_date": "2024-01-10",
                "ticker": None,
            },
            {
                "filer_id": "house_nancy_pelosi",
                "transaction_date": "2024-01-01",
                "filing_date": "2024-01-10",
                "ticker": "N/A",
            },
        ],
    )
    assert parse_filer_json(raw) == []


def test_member_name_falls_back_to_filer_id_slug_when_filer_object_absent():
    raw = _filer_json(
        {},  # no id/full_name in the filer object
        [
            {
                "filer_id": "house_josh_gottheimer",
                "transaction_date": "2024-01-01",
                "filing_date": "2024-01-10",
                "ticker": "MSFT",
            }
        ],
    )
    trades = parse_filer_json(raw)
    assert trades[0].member_name == "Josh Gottheimer"
    assert trades[0].chamber == "house"


def test_parse_filer_json_malformed_input_returns_empty():
    assert parse_filer_json(b"not json") == []
    assert parse_filer_json(b"[]") == []
    assert parse_filer_json(b'{"filer": {}, "trades": "not-a-list"}') == []


# --- CongressStore -----------------------------------------------------


def test_store_refresh_inserts_and_is_idempotent(tmp_path):
    db_path = str(tmp_path / "congress.db")
    filer_raw = _filer_json(
        HOUSE_FILER,
        [
            {
                "filer_id": "house_nancy_pelosi",
                "transaction_date": "2024-01-01",
                "filing_date": "2024-01-10",
                "ticker": "MSFT",
                "owner": "SP",
                "asset_type": "ST",
                "transaction_type": "Purchase",
                "amount_range_low": 15001,
                "amount_range_high": 50000,
                "is_late": 0,
                "days_to_file": 9,
            }
        ],
    )
    list_fetch = lambda url: _index_bytes(["house_nancy_pelosi.json"])  # noqa: E731
    fetch = lambda url: filer_raw  # noqa: E731

    store = CongressStore(db_path=db_path)
    inserted = store.refresh(fetch=fetch, list_fetch=list_fetch)
    assert inserted == 1

    inserted2 = store.refresh(fetch=fetch, list_fetch=list_fetch)
    assert inserted2 == 0


def test_store_refresh_caches_raw_json_under_db_dir_and_reuses_fresh_cache(tmp_path):
    db_path = str(tmp_path / "congress.db")
    filer_raw = _filer_json(HOUSE_FILER, [])
    calls = []

    def fetch(url):
        calls.append(url)
        return filer_raw

    list_fetch = lambda url: _index_bytes(["house_nancy_pelosi.json"])  # noqa: E731

    store = CongressStore(db_path=db_path)
    store.refresh(fetch=fetch, list_fetch=list_fetch)
    cache_file = tmp_path / "cache" / "congress" / "house_nancy_pelosi.json"
    assert cache_file.exists()
    assert len(calls) == 1

    # Second refresh should reuse the (fresh) cache file, not re-fetch.
    store.refresh(fetch=fetch, list_fetch=list_fetch)
    assert len(calls) == 1


def test_store_refresh_respects_max_filers(tmp_path):
    db_path = str(tmp_path / "congress.db")
    filer_raw = _filer_json(HOUSE_FILER, [])
    list_fetch = lambda url: _index_bytes(["a.json", "b.json", "c.json"])  # noqa: E731
    fetched = []

    def fetch(url):
        fetched.append(url)
        return filer_raw

    store = CongressStore(db_path=db_path)
    store.refresh(fetch=fetch, list_fetch=list_fetch, max_filers=1)
    assert len(fetched) == 1


def test_store_trades_date_range_filter(tmp_path):
    db_path = str(tmp_path / "congress.db")
    filer_raw = _filer_json(
        HOUSE_FILER,
        [
            {
                "filer_id": "house_nancy_pelosi",
                "transaction_date": "2024-01-01",
                "filing_date": "2024-01-10",
                "ticker": "MSFT",
            },
            {
                "filer_id": "house_nancy_pelosi",
                "transaction_date": "2024-06-01",
                "filing_date": "2024-06-10",
                "ticker": "AAPL",
            },
        ],
    )
    list_fetch = lambda url: _index_bytes(["house_nancy_pelosi.json"])  # noqa: E731
    fetch = lambda url: filer_raw  # noqa: E731

    store = CongressStore(db_path=db_path)
    store.refresh(fetch=fetch, list_fetch=list_fetch)

    all_rows = store.trades()
    assert len(all_rows) == 2

    in_range = store.trades(start="2024-01-01", end="2024-01-31")
    assert len(in_range) == 1
    assert in_range[0].ticker == "MSFT"

    out_of_range = store.trades(start="2025-01-01", end="2025-12-31")
    assert out_of_range == []


def test_fetch_sends_user_agent(monkeypatch):
    from momentum_desk.congress import loader as loader_mod

    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"{}"

    def fake_urlopen(request, timeout=30):
        captured["headers"] = dict(request.headers)
        captured["url"] = request.full_url
        return FakeResponse()

    monkeypatch.setattr(loader_mod.urllib.request, "urlopen", fake_urlopen)

    result = loader_mod._fetch_url(FILER_INDEX_URL)
    assert result == b"{}"
    # urllib normalizes header casing to Title-Case
    assert captured["headers"].get("User-agent") == CONGRESS_UA


def test_refresh_logs_per_year_density(tmp_path, caplog):
    db_path = str(tmp_path / "congress.db")
    filer_raw = _filer_json(
        HOUSE_FILER,
        [
            {
                "filer_id": "house_nancy_pelosi",
                "transaction_date": "2024-01-01",
                "filing_date": "2024-01-10",
                "ticker": "MSFT",
            },
            {
                "filer_id": "house_nancy_pelosi",
                "transaction_date": "2023-06-01",
                "filing_date": "2023-06-10",
                "ticker": "AAPL",
            },
        ],
    )
    list_fetch = lambda url: _index_bytes(["house_nancy_pelosi.json"])  # noqa: E731
    fetch = lambda url: filer_raw  # noqa: E731

    store = CongressStore(db_path=db_path)
    with caplog.at_level(logging.INFO, logger="momentum_desk.congress.loader"):
        store.refresh(fetch=fetch, list_fetch=list_fetch)

    assert "2024" in caplog.text
    assert "2023" in caplog.text
