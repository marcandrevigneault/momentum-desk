"""EDGAR quarterly form345 bulk loader.

The SEC publishes one ZIP per calendar quarter containing every Form 3/4/5
filed in that period as a set of pipe-free TSV tables (SUBMISSION,
REPORTINGOWNER, NONDERIV_TRANS, ...). This module downloads those ZIPs,
joins the three tables we care about on ACCESSION_NUMBER, and lands the
result as ``InsiderFiling`` rows in a local SQLite store — the durable,
queryable base that Task 1's pure signal functions and later tasks
(market-cap/news enrichment, backtesting) read from without re-hitting
the network. Loading is idempotent per quarter so a cron-style backfill
can be re-run safely.
"""
from __future__ import annotations

import csv
import io
import os
import sqlite3
import time
import urllib.request
import zipfile
from datetime import date, datetime

from .models import InsiderFiling

SEC_UA = "momentum-desk marcandre.vigneault.96@gmail.com"

ZIP_URL_PATTERNS = [
    "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{yq}_form345.zip",
    "https://www.sec.gov/files/datastandardsinnovation/data/insider-transactions-data-sets/{yq}_form345.zip",
]  # {yq} like "2025q1"; try in order, first 200 wins

_SCHEMA = """
CREATE TABLE IF NOT EXISTS filings (
  accession TEXT, symbol TEXT, filed TEXT, trans_date TEXT, code TEXT,
  shares REAL, price REAL, owner_name TEXT,
  is_ceo INTEGER, is_cfo INTEGER, is_officer INTEGER, is_director INTEGER,
  is_ten_pct INTEGER, officer_title TEXT, tenb5_1 INTEGER, shares_owned_after REAL,
  PRIMARY KEY (accession, owner_name, trans_date, code, shares, price)
);
CREATE INDEX IF NOT EXISTS idx_filings_filed ON filings(filed);
CREATE TABLE IF NOT EXISTS quarters_loaded (yq TEXT PRIMARY KEY);
"""


def _normalize_date(raw: str) -> str:
    """Accepts ISO (YYYY-MM-DD) or EDGAR's DD-MON-YYYY; returns ISO. Best
    effort — an unparsable value is returned unchanged rather than raising,
    since a bad date shouldn't sink an otherwise-good row."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        pass
    try:
        return datetime.strptime(raw, "%d-%b-%Y").date().isoformat()
    except ValueError:
        return raw


def _to_float(raw: str | None, default: float = 0.0) -> float:
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _to_bool_flag(raw: str | None) -> bool:
    return (raw or "").strip() in ("1", "true", "True", "TRUE", "Y", "y")


def _owner_flags(owner: dict[str, str]) -> tuple[bool, bool, bool, str]:
    """Derives (is_director, is_officer, is_ten_pct, officer_title) from a
    REPORTINGOWNER row. Two shapes seen in the wild:

    - The brief's expected shape: discrete ISDIRECTOR/ISOFFICER/
      ISTENPERCENTOWNER/OFFICERTITLE columns.
    - The real 2024q1 shape: a single comma-joined RPTOWNER_RELATIONSHIP
      column (e.g. "Director,Officer") plus RPTOWNER_TITLE for the title.

    Falls back to all-False/blank if neither shape is present, never
    raises on a missing column."""
    has_discrete_flags = any(
        key in owner for key in ("ISDIRECTOR", "ISOFFICER", "ISTENPERCENTOWNER", "OFFICERTITLE")
    )
    if has_discrete_flags:
        return (
            _to_bool_flag(owner.get("ISDIRECTOR")),
            _to_bool_flag(owner.get("ISOFFICER")),
            _to_bool_flag(owner.get("ISTENPERCENTOWNER")),
            (owner.get("OFFICERTITLE") or "").strip(),
        )

    relationship = (owner.get("RPTOWNER_RELATIONSHIP") or "").strip()
    roles = {part.strip() for part in relationship.split(",") if part.strip()}
    title = (owner.get("RPTOWNER_TITLE") or "").strip()
    return ("Director" in roles, "Officer" in roles, "TenPercentOwner" in roles, title)


def _read_tsv(zf: zipfile.ZipFile, name: str) -> list[dict[str, str]]:
    """Tolerant TSV reader: missing file -> empty list, never KeyError on a
    missing column (DictReader supplies None for absent keys, handled by
    callers via .get-style defaults)."""
    try:
        raw = zf.open(name)
    except KeyError:
        return []
    with io.TextIOWrapper(raw, encoding="latin-1") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        return [dict(row) for row in reader]


def _find_10b5_column(fieldnames: list[str] | None) -> str | None:
    if not fieldnames:
        return None
    for name in fieldnames:
        if "10B5" in name.upper():
            return name
    return None


def parse_quarter_zip(zip_bytes: bytes) -> list[InsiderFiling]:
    """Parse SUBMISSION.tsv + REPORTINGOWNER.tsv + NONDERIV_TRANS.tsv from one
    quarterly ZIP into InsiderFiling rows (one row per non-derivative
    transaction line, joined on ACCESSION_NUMBER)."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        submissions = _read_tsv(zf, "SUBMISSION.tsv")
        owners = _read_tsv(zf, "REPORTINGOWNER.tsv")
        transactions = _read_tsv(zf, "NONDERIV_TRANS.tsv")

    sub_by_acc: dict[str, dict[str, str]] = {}
    for row in submissions:
        acc = (row.get("ACCESSION_NUMBER") or "").strip()
        if acc:
            sub_by_acc[acc] = row

    owner_by_acc: dict[str, dict[str, str]] = {}
    for row in owners:
        acc = (row.get("ACCESSION_NUMBER") or "").strip()
        if acc:
            owner_by_acc[acc] = row

    trans_10b5_col = _find_10b5_column(list(transactions[0].keys()) if transactions else None)
    sub_10b5_col = _find_10b5_column(list(submissions[0].keys()) if submissions else None)

    out: list[InsiderFiling] = []
    for trow in transactions:
        acc = (trow.get("ACCESSION_NUMBER") or "").strip()
        if not acc:
            continue
        sub = sub_by_acc.get(acc, {})
        owner = owner_by_acc.get(acc, {})

        symbol = (sub.get("ISSUERTRADINGSYMBOL") or "").strip()
        doc_type = (sub.get("DOCUMENT_TYPE") or "").strip()
        if not symbol or doc_type not in ("4", "4/A"):
            continue

        shares = _to_float(trow.get("TRANS_SHARES"))
        price = _to_float(trow.get("TRANS_PRICEPERSHARE"))
        if shares <= 0 or price <= 0:
            continue

        is_director, is_officer, is_ten_pct, officer_title = _owner_flags(owner)
        title_lower = officer_title.lower()
        is_ceo = "chief executive" in title_lower or "ceo" in title_lower
        is_cfo = "chief financial" in title_lower or "cfo" in title_lower

        tenb5_1 = False
        if trans_10b5_col:
            tenb5_1 = _to_bool_flag(trow.get(trans_10b5_col))
        elif sub_10b5_col:
            tenb5_1 = _to_bool_flag(sub.get(sub_10b5_col))

        out.append(
            InsiderFiling(
                accession=acc,
                symbol=symbol,
                filed=_normalize_date(sub.get("FILING_DATE", "")),
                trans_date=_normalize_date(trow.get("TRANS_DATE", "")),
                code=(trow.get("TRANS_CODE") or "").strip(),
                shares=shares,
                price=price,
                owner_name=(owner.get("RPTOWNERNAME") or "").strip(),
                is_ceo=is_ceo,
                is_cfo=is_cfo,
                is_officer=is_officer,
                is_director=is_director,
                is_ten_pct=is_ten_pct,
                officer_title=officer_title,
                tenb5_1=tenb5_1,
                shares_owned_after=_to_float(trow.get("SHRS_OWND_FOLWNG_TRANS")),
            )
        )
    return out


def _fetch_url(url: str) -> bytes:
    """Download bytes from `url` sending the required SEC User-Agent."""
    request = urllib.request.Request(url, headers={"User-Agent": SEC_UA})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


class EdgarStore:
    def __init__(self, db_path: str = "data/insider.db") -> None:
        self.db_path = db_path
        _ensure_parent_dir(db_path)
        self._conn = sqlite3.connect(db_path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def load_quarter(self, year: int, quarter: int, *, fetch=None) -> int:
        """Idempotent: skips if the quarter is already recorded. Downloads via
        urllib with SEC_UA, caches the raw zip under the db's directory at
        <db dir>/cache/edgar/{yq}.zip, parses, inserts. Returns rows inserted.
        `fetch(url) -> bytes` injectable for tests."""
        yq = f"{year}q{quarter}"
        cur = self._conn.execute("SELECT 1 FROM quarters_loaded WHERE yq = ?", (yq,))
        if cur.fetchone() is not None:
            return 0

        zip_bytes = self._get_quarter_zip(yq, fetch=fetch)
        rows = parse_quarter_zip(zip_bytes)

        inserted = 0
        for f in rows:
            cur = self._conn.execute(
                """
                INSERT OR IGNORE INTO filings (
                  accession, symbol, filed, trans_date, code, shares, price,
                  owner_name, is_ceo, is_cfo, is_officer, is_director,
                  is_ten_pct, officer_title, tenb5_1, shares_owned_after
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f.accession, f.symbol, f.filed, f.trans_date, f.code,
                    f.shares, f.price, f.owner_name, int(f.is_ceo), int(f.is_cfo),
                    int(f.is_officer), int(f.is_director), int(f.is_ten_pct),
                    f.officer_title, int(f.tenb5_1), f.shares_owned_after,
                ),
            )
            inserted += cur.rowcount

        self._conn.execute("INSERT OR IGNORE INTO quarters_loaded (yq) VALUES (?)", (yq,))
        self._conn.commit()
        return inserted

    def _get_quarter_zip(self, yq: str, *, fetch=None) -> bytes:
        db_dir = os.path.dirname(self.db_path) or "."
        cache_path = os.path.join(db_dir, "cache", "edgar", f"{yq}.zip")
        _ensure_parent_dir(cache_path)
        try:
            with open(cache_path, "rb") as fh:
                return fh.read()
        except FileNotFoundError:
            pass

        fetch_fn = fetch or _fetch_url
        last_error: Exception | None = None
        for pattern in ZIP_URL_PATTERNS:
            url = pattern.format(yq=yq)
            try:
                zip_bytes = fetch_fn(url)
                break
            except Exception as exc:  # noqa: BLE001 - try next URL pattern
                last_error = exc
                zip_bytes = None
        else:
            zip_bytes = None

        if zip_bytes is None:
            raise RuntimeError(f"could not fetch {yq} form345 zip from any known URL") from last_error

        with open(cache_path, "wb") as fh:
            fh.write(zip_bytes)
        return zip_bytes

    def load_range(self, start_year: int, *, fetch=None) -> None:
        """Loads every quarter from start_year Q1 through the current
        (year, quarter), sleeping briefly between downloads to stay far
        below SEC's rate limits."""
        today = date.today()
        current_quarter = (today.month - 1) // 3 + 1
        for year in range(start_year, today.year + 1):
            last_q = current_quarter if year == today.year else 4
            for quarter in range(1, last_q + 1):
                self.load_quarter(year, quarter, fetch=fetch)
                if fetch is None:
                    time.sleep(0.5)

    def filings(self, *, start: str | None = None, end: str | None = None) -> list[InsiderFiling]:
        query = "SELECT accession, symbol, filed, trans_date, code, shares, price, owner_name, " \
                "is_ceo, is_cfo, is_officer, is_director, is_ten_pct, officer_title, tenb5_1, " \
                "shares_owned_after FROM filings"
        clauses = []
        params: list[str] = []
        if start is not None:
            clauses.append("filed >= ?")
            params.append(start)
        if end is not None:
            clauses.append("filed <= ?")
            params.append(end)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY filed"

        rows = self._conn.execute(query, params).fetchall()
        return [
            InsiderFiling(
                accession=r[0], symbol=r[1], filed=r[2], trans_date=r[3], code=r[4],
                shares=r[5], price=r[6], owner_name=r[7], is_ceo=bool(r[8]), is_cfo=bool(r[9]),
                is_officer=bool(r[10]), is_director=bool(r[11]), is_ten_pct=bool(r[12]),
                officer_title=r[13], tenb5_1=bool(r[14]), shares_owned_after=r[15],
            )
            for r in rows
        ]


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
