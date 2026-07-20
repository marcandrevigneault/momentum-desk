"""kadoa congress-trading-monitor loader (STOCK Act periodic transaction reports).

kadoa-org/congress-trading-monitor publishes one JSON file per filer
(House member, Senator, or executive-branch OGE filer) at a fixed GitHub
raw URL, plus a GitHub-contents-API directory listing of those files. This
module downloads the listing and each filer JSON, normalizes rows into
``CongressTrade``, and lands them in a local SQLite store — the durable,
queryable base later tasks (signal building, backtesting) read from
without re-hitting the network. Refresh is idempotent (``INSERT OR
IGNORE``) so a cron-style backfill can be re-run safely.

Real-data note (see task report for the full write-up): the dataset is
NOT internally consistent. House-chamber filer files use abbreviated
codes (``owner: "SP"/"JT"``, ``asset_type: "ST"/"OP"``); Senate-chamber
filer files use full words (``owner: "Joint"/"Self"/"Spouse"/"Child"``,
``asset_type: "Stock"/"Other"``). ``owner`` is normalized to a small
canonical set (SP/JT/DC/SELF) at parse time so downstream signal code
doesn't need to special-case chambers; ``asset_type`` is stored verbatim
(raw code) since the spec only asks for pass-through there.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import urllib.request
from dataclasses import dataclass
from datetime import UTC, date, datetime

from ..insider.edgar import normalize_symbol

CONGRESS_UA = "momentum-desk marcandre.vigneault.96@gmail.com"

FILER_INDEX_URL = "https://api.github.com/repos/kadoa-org/congress-trading-monitor/contents/public/data/filer"
FILER_RAW_URL = "https://raw.githubusercontent.com/kadoa-org/congress-trading-monitor/main/public/data/filer/{name}"

logger = logging.getLogger(__name__)

_CACHE_MAX_AGE_SECONDS = 24 * 3600

_OWNER_MAP = {
    "SP": "SP",
    "SPOUSE": "SP",
    "JT": "JT",
    "JOINT": "JT",
    "DC": "DC",
    "CHILD": "DC",
    "DEPENDENT CHILD": "DC",
    "DEPENDENT_CHILD": "DC",
    "SELF": "SELF",
}

_CHAMBER_PREFIXES = {"house_": "house", "senate_": "senate"}


@dataclass
class CongressTrade:
    """One STOCK Act periodic-transaction-report line item."""

    filer_id: str
    member_name: str
    chamber: str            # "house" | "senate" | "other"
    ticker: str
    transaction_date: str   # ISO
    filing_date: str        # ISO
    owner: str = "SELF"     # "SP" | "JT" | "SELF" | "DC" | ""
    asset_type: str = ""    # raw code, e.g. "ST", "OP", "Stock"
    transaction_type: str = ""   # "Purchase" | "Sale" | ... (case-normalized)
    amount_low: float = 0.0
    amount_high: float = 0.0
    is_late: bool = False
    days_to_file: int = 0


_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
  filer_id TEXT, member_name TEXT, chamber TEXT, ticker TEXT,
  transaction_date TEXT, filing_date TEXT, owner TEXT, asset_type TEXT,
  transaction_type TEXT, amount_low REAL, amount_high REAL,
  is_late INTEGER, days_to_file INTEGER,
  PRIMARY KEY (filer_id, ticker, transaction_date, filing_date, amount_low, transaction_type, owner)
);
CREATE INDEX IF NOT EXISTS idx_trades_filing ON trades(filing_date);
CREATE TABLE IF NOT EXISTS refreshes (at TEXT);
"""


def _to_float(raw, default: float = 0.0) -> float:
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _to_int(raw, default: int = 0) -> int:
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            return default


def _to_bool(raw) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    return str(raw or "").strip().lower() in ("1", "true", "yes", "y")


def _valid_iso_date(raw) -> str | None:
    """Kadoa dates are already ISO; validated (not reformatted) and the row
    dropped on failure rather than sinking the whole file."""
    if not raw or not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw.strip()).isoformat()
    except ValueError:
        return None


def _chamber_from_filer_id(filer_id: str) -> str:
    for prefix, chamber in _CHAMBER_PREFIXES.items():
        if filer_id.startswith(prefix):
            return chamber
    return "other"


def _name_from_filer_id_slug(filer_id: str) -> str:
    slug = filer_id
    for prefix in ("house_", "senate_", "oge_"):
        if slug.startswith(prefix):
            slug = slug[len(prefix):]
            break
    return slug.replace("_", " ").replace("-", " ").strip().title()


def _normalize_owner(raw) -> str:
    if not raw or not isinstance(raw, str):
        return "SELF"
    key = raw.strip().upper()
    if not key:
        return "SELF"
    return _OWNER_MAP.get(key, "")


def _normalize_transaction_type(raw) -> str:
    if not raw or not isinstance(raw, str):
        return ""
    return raw.strip().title()


def parse_filer_json(raw: bytes | str) -> list[CongressTrade]:
    """One kadoa per-filer JSON (``{"filer": {...}, "trades": [...]}``) ->
    trades. Tolerant: missing key -> default, never KeyError; rows without
    a resolvable ticker or valid ISO transaction/filing date are skipped;
    member_name falls back to the filer_id slug when the filer object is
    absent/incomplete; chamber is derived from the filer_id prefix."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, dict):
        return []

    filer = data.get("filer")
    if not isinstance(filer, dict):
        filer = {}
    trades_raw = data.get("trades")
    if not isinstance(trades_raw, list):
        return []

    default_filer_id = (filer.get("id") or "").strip()
    full_name = (filer.get("full_name") or "").strip()

    out: list[CongressTrade] = []
    for row in trades_raw:
        if not isinstance(row, dict):
            continue

        filer_id = (row.get("filer_id") or default_filer_id or "").strip()

        ticker = normalize_symbol(row.get("ticker") or "")
        if ticker is None:
            continue

        transaction_date = _valid_iso_date(row.get("transaction_date"))
        filing_date = _valid_iso_date(row.get("filing_date"))
        if transaction_date is None or filing_date is None:
            continue

        member_name = full_name or _name_from_filer_id_slug(filer_id)
        chamber = _chamber_from_filer_id(filer_id)

        out.append(
            CongressTrade(
                filer_id=filer_id,
                member_name=member_name,
                chamber=chamber,
                ticker=ticker,
                transaction_date=transaction_date,
                filing_date=filing_date,
                owner=_normalize_owner(row.get("owner")),
                asset_type=(row.get("asset_type") or "").strip(),
                transaction_type=_normalize_transaction_type(row.get("transaction_type")),
                amount_low=_to_float(row.get("amount_range_low")),
                amount_high=_to_float(row.get("amount_range_high")),
                is_late=_to_bool(row.get("is_late")),
                days_to_file=_to_int(row.get("days_to_file")),
            )
        )
    return out


def _fetch_url(url: str) -> bytes:
    """Download bytes from `url` sending the required UA header."""
    request = urllib.request.Request(url, headers={"User-Agent": CONGRESS_UA})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


class CongressStore:
    def __init__(self, db_path: str = "data/congress.db") -> None:
        self.db_path = db_path
        _ensure_parent_dir(db_path)
        self._conn = sqlite3.connect(db_path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def refresh(self, *, fetch=None, list_fetch=None, max_filers: int | None = None) -> int:
        """Fetch the filer index (GitHub contents API), then each filer
        JSON (cache raw under <db dir>/cache/congress/<name>; reuse cache
        when the file is younger than 24h), parse, INSERT OR IGNORE.
        Returns rows inserted. `fetch(url)->bytes` and
        `list_fetch(url)->bytes` are injectable for tests. Logs per-year
        row density after refresh (spec caveat: dataset is a vendor
        showcase, validate row density per year)."""
        list_fetch_fn = list_fetch or _fetch_url
        index_raw = list_fetch_fn(FILER_INDEX_URL)
        try:
            entries = json.loads(index_raw)
        except (json.JSONDecodeError, TypeError):
            entries = []
        if not isinstance(entries, list):
            entries = []

        names = [e.get("name") for e in entries if isinstance(e, dict) and e.get("name")]
        if max_filers is not None:
            names = names[:max_filers]

        inserted_total = 0
        for name in names:
            raw = self._get_filer_raw(name, fetch=fetch)
            for t in parse_filer_json(raw):
                ticker = normalize_symbol(t.ticker)
                if ticker is None:
                    continue
                cur = self._conn.execute(
                    """
                    INSERT OR IGNORE INTO trades (
                      filer_id, member_name, chamber, ticker, transaction_date,
                      filing_date, owner, asset_type, transaction_type,
                      amount_low, amount_high, is_late, days_to_file
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        t.filer_id, t.member_name, t.chamber, ticker, t.transaction_date,
                        t.filing_date, t.owner, t.asset_type, t.transaction_type,
                        t.amount_low, t.amount_high, int(t.is_late), t.days_to_file,
                    ),
                )
                inserted_total += cur.rowcount

        self._conn.execute(
            "INSERT INTO refreshes (at) VALUES (?)",
            (datetime.now(UTC).isoformat(timespec="seconds"),),
        )
        self._conn.commit()
        self._log_year_density()
        return inserted_total

    def _get_filer_raw(self, name: str, *, fetch=None) -> bytes:
        db_dir = os.path.dirname(self.db_path) or "."
        cache_path = os.path.join(db_dir, "cache", "congress", name)
        _ensure_parent_dir(cache_path)
        try:
            mtime = os.path.getmtime(cache_path)
            if time.time() - mtime < _CACHE_MAX_AGE_SECONDS:
                with open(cache_path, "rb") as fh:
                    return fh.read()
        except FileNotFoundError:
            pass

        fetch_fn = fetch or _fetch_url
        raw = fetch_fn(FILER_RAW_URL.format(name=name))
        with open(cache_path, "wb") as fh:
            fh.write(raw)
        if fetch is None:
            time.sleep(0.2)  # sequential + polite when hitting the real network
        return raw

    def _log_year_density(self) -> None:
        rows = self._conn.execute(
            "SELECT substr(filing_date, 1, 4) AS yr, COUNT(*) FROM trades "
            "GROUP BY yr ORDER BY yr"
        ).fetchall()
        density = ", ".join(f"{yr}={count}" for yr, count in rows)
        logger.info("congress trades per-year density: %s", density or "(empty)")

    def trades(self, *, start: str | None = None, end: str | None = None) -> list[CongressTrade]:
        query = (
            "SELECT filer_id, member_name, chamber, ticker, transaction_date, filing_date, "
            "owner, asset_type, transaction_type, amount_low, amount_high, is_late, days_to_file "
            "FROM trades"
        )
        clauses = []
        params: list[str] = []
        if start is not None:
            clauses.append("filing_date >= ?")
            params.append(start)
        if end is not None:
            clauses.append("filing_date <= ?")
            params.append(end)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY filing_date"

        rows = self._conn.execute(query, params).fetchall()
        out: list[CongressTrade] = []
        for r in rows:
            # Mirror EdgarStore.filings(): re-normalize at read time too, so
            # already-populated rows stay clean regardless of when/how they
            # were inserted.
            ticker = normalize_symbol(r[3] or "")
            if ticker is None:
                continue
            out.append(
                CongressTrade(
                    filer_id=r[0], member_name=r[1], chamber=r[2], ticker=ticker,
                    transaction_date=r[4], filing_date=r[5], owner=r[6], asset_type=r[7],
                    transaction_type=r[8], amount_low=r[9], amount_high=r[10],
                    is_late=bool(r[11]), days_to_file=r[12],
                )
            )
        return out


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
