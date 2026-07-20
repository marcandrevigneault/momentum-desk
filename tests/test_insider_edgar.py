"""Tests for the EDGAR form345 bulk loader and SQLite store.

Fixtures are tiny in-memory ZIPs (zipfile + io.BytesIO) built from inline TSV
strings, so no binary fixtures or network access are needed for the unit
tests. The one real-quarter check is done manually (see task report), not
here.
"""
from __future__ import annotations

import io
import zipfile

import pytest

from momentum_desk.insider.edgar import SEC_UA, EdgarStore, normalize_symbol, parse_quarter_zip

SUBMISSION_TSV = (
    "ACCESSION_NUMBER\tFILING_DATE\tISSUERTRADINGSYMBOL\tDOCUMENT_TYPE\n"
    "acc-1\t04-JAN-2024\tACME\t4\n"
    "acc-2\t2024-01-05\tWIDG\t4\n"
    "acc-3\t2024-01-06\t\t4\n"
)

REPORTINGOWNER_TSV = (
    "ACCESSION_NUMBER\tRPTOWNERNAME\tISDIRECTOR\tISOFFICER\tISTENPERCENTOWNER\tOFFICERTITLE\n"
    "acc-1\tJane Doe\t0\t1\t0\tChief Executive Officer\n"
    "acc-2\tJohn Roe\t1\t0\t0\t\n"
    "acc-3\tNo Symbol Owner\t0\t1\t0\tVP\n"
)

NONDERIV_TRANS_TSV = (
    "ACCESSION_NUMBER\tTRANS_DATE\tTRANS_CODE\tTRANS_SHARES\tTRANS_PRICEPERSHARE\tSHRS_OWND_FOLWNG_TRANS\n"
    "acc-1\t2024-01-04\tP\t1000\t12.5\t5000\n"
    "acc-2\t2024-01-05\tS\t500\t20.0\t1000\n"
    "acc-3\t2024-01-06\tP\t100\t5.0\t200\n"
)


def make_zip(
    submission: str = SUBMISSION_TSV,
    reportingowner: str = REPORTINGOWNER_TSV,
    nonderiv: str = NONDERIV_TRANS_TSV,
) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("SUBMISSION.tsv", submission)
        zf.writestr("REPORTINGOWNER.tsv", reportingowner)
        zf.writestr("NONDERIV_TRANS.tsv", nonderiv)
    return buf.getvalue()


def test_parse_quarter_zip_joins_and_normalizes():
    rows = parse_quarter_zip(make_zip())
    assert len(rows) == 2  # acc-3 dropped for blank symbol
    f = next(r for r in rows if r.accession == "acc-1")
    assert f.accession == "acc-1"
    assert f.symbol == "ACME"
    assert f.filed == "2024-01-04"
    assert f.trans_date == "2024-01-04"
    assert f.code == "P"
    assert f.shares == 1000
    assert f.price == 12.5
    assert f.owner_name == "Jane Doe"
    assert f.is_ceo is True
    assert f.is_cfo is False
    assert f.is_officer is True
    assert f.is_director is False
    assert f.officer_title == "Chief Executive Officer"
    assert f.shares_owned_after == 5000


def test_parse_skips_blank_symbol_and_non_form4():
    rows = parse_quarter_zip(make_zip())
    accessions = {f.accession for f in rows}
    # acc-2 has no matching row because it's a non-P but should still parse if
    # it were an S? no -- acc-2 is Form 4 with symbol WIDG but code S: kept by
    # the parser (code filtering is a signals-layer concern), only the blank
    # symbol (acc-3) must be dropped here.
    assert "acc-3" not in accessions

    submission_non_form4 = SUBMISSION_TSV.replace("acc-2\t2024-01-05\tWIDG\t4", "acc-2\t2024-01-05\tWIDG\t3")
    rows2 = parse_quarter_zip(
        make_zip(submission=submission_non_form4)
    )
    accessions2 = {f.accession for f in rows2}
    assert "acc-2" not in accessions2


def test_store_load_quarter_idempotent(tmp_path):
    db_path = str(tmp_path / "insider.db")
    zip_bytes = make_zip()
    store = EdgarStore(db_path=db_path)
    inserted = store.load_quarter(2024, 1, fetch=lambda url: zip_bytes)
    assert inserted == 2  # acc-1 (P) and acc-2 (S), acc-3 dropped

    inserted2 = store.load_quarter(2024, 1, fetch=lambda url: zip_bytes)
    assert inserted2 == 0


def test_store_filings_date_range(tmp_path):
    db_path = str(tmp_path / "insider.db")
    zip_bytes = make_zip()
    store = EdgarStore(db_path=db_path)
    store.load_quarter(2024, 1, fetch=lambda url: zip_bytes)

    all_rows = store.filings()
    assert len(all_rows) == 2

    in_range = store.filings(start="2024-01-04", end="2024-01-04")
    assert len(in_range) == 1
    assert in_range[0].accession == "acc-1"

    out_of_range = store.filings(start="2024-02-01", end="2024-02-28")
    assert out_of_range == []


SUBMISSION_TSV_RELATIONSHIP = (
    "ACCESSION_NUMBER\tFILING_DATE\tISSUERTRADINGSYMBOL\tDOCUMENT_TYPE\n"
    "acc-1\t2024-04-01\tFOO\t4\n"
    "acc-2\t2024-04-02\tBAR\t4\n"
    "acc-3\t2024-04-03\tBAZ\t4\n"
)

# Real-world SEC shape: a comma-joined RPTOWNER_RELATIONSHIP column plus a
# separate RPTOWNER_TITLE, instead of the brief's discrete ISDIRECTOR/
# ISOFFICER/ISTENPERCENTOWNER/OFFICERTITLE columns.
REPORTINGOWNER_TSV_RELATIONSHIP = (
    "ACCESSION_NUMBER\tRPTOWNERNAME\tRPTOWNER_RELATIONSHIP\tRPTOWNER_TITLE\n"
    "acc-1\tCindy CFO\tOfficer\tChief Financial Officer\n"
    "acc-2\tDan Director\tDirector\t\n"
    "acc-3\tTina TenPercent\tTenPercentOwner\t\n"
)

# Also covers the AFF10B5ONE ("Rule 10b5-1 trading plan") flag: set on acc-1,
# explicitly unset on acc-2, blank (absent) on acc-3.
NONDERIV_TRANS_TSV_10B5 = (
    "ACCESSION_NUMBER\tTRANS_DATE\tTRANS_CODE\tTRANS_SHARES\tTRANS_PRICEPERSHARE\t"
    "SHRS_OWND_FOLWNG_TRANS\tAFF10B5ONE\n"
    "acc-1\t2024-04-01\tP\t1000\t10.0\t5000\t1\n"
    "acc-2\t2024-04-02\tS\t500\t20.0\t1000\t0\n"
    "acc-3\t2024-04-03\tP\t100\t5.0\t200\t\n"
)


def test_parse_real_world_reportingowner_relationship_shape_and_10b5_1():
    rows = parse_quarter_zip(
        make_zip(
            submission=SUBMISSION_TSV_RELATIONSHIP,
            reportingowner=REPORTINGOWNER_TSV_RELATIONSHIP,
            nonderiv=NONDERIV_TRANS_TSV_10B5,
        )
    )
    by_acc = {r.accession: r for r in rows}
    assert set(by_acc) == {"acc-1", "acc-2", "acc-3"}

    cfo = by_acc["acc-1"]
    assert cfo.is_officer is True
    assert cfo.is_cfo is True
    assert cfo.is_director is False
    assert cfo.is_ten_pct is False
    assert cfo.officer_title == "Chief Financial Officer"
    assert cfo.tenb5_1 is True

    director = by_acc["acc-2"]
    assert director.is_director is True
    assert director.is_officer is False
    assert director.is_cfo is False
    assert director.is_ten_pct is False
    assert director.tenb5_1 is False

    ten_pct = by_acc["acc-3"]
    assert ten_pct.is_ten_pct is True
    assert ten_pct.is_officer is False
    assert ten_pct.is_director is False
    assert ten_pct.tenb5_1 is False


def test_fetch_sends_user_agent(monkeypatch):
    from momentum_desk.insider import edgar as edgar_mod

    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"zip-bytes"

    def fake_urlopen(request, timeout=30):
        captured["headers"] = dict(request.headers)
        captured["url"] = request.full_url
        return FakeResponse()

    monkeypatch.setattr(edgar_mod.urllib.request, "urlopen", fake_urlopen)

    result = edgar_mod._fetch_url(edgar_mod.ZIP_URL_PATTERNS[0].format(yq="2024q1"))
    assert result == b"zip-bytes"
    # urllib normalizes header casing to Title-Case
    assert captured["headers"].get("User-agent") == SEC_UA


# --- normalize_symbol ----------------------------------------------------
#
# EDGAR's ISSUERTRADINGSYMBOL is free text, not a validated ticker field:
# dual-class filers report both symbols comma-joined ("HEI, HEI.A"), some
# filers lowercase it, and some filers just leave junk ("N/A"). Values like
# these flowed unencoded into Polygon URL paths and crashed real runs with
# `http.client.InvalidURL` — normalize_symbol is the fix, applied both at
# ingest (parse_quarter_zip) and at read time (EdgarStore.filings) so the
# already-populated 571k-row DB doesn't need a re-ingest.
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("HEI, HEI.A", "HEI"),
        ("BRK.B", "BRK.B"),
        ("hei", "HEI"),
        ("N/A", None),          # "/" fails the character class
        ("", None),
        (None, None),
        ("TOOLONGSYMBOL", None),  # 13 chars, exceeds the 10-char cap
    ],
)
def test_normalize_symbol(raw, expected):
    assert normalize_symbol(raw) == expected


# --- EdgarStore.filings() read-time normalization -------------------------


def test_filings_normalizes_dirty_symbol_at_read_time(tmp_path):
    """Simulates the already-populated DB: rows inserted with EDGAR's raw,
    unnormalized symbol (bypassing parse_quarter_zip's ingest-time
    normalization) must still come back clean from filings(), since the
    571k existing rows were never re-ingested."""
    db_path = str(tmp_path / "insider.db")
    store = EdgarStore(db_path=db_path)
    store._conn.execute(
        """
        INSERT INTO filings (
          accession, symbol, filed, trans_date, code, shares, price,
          owner_name, is_ceo, is_cfo, is_officer, is_director,
          is_ten_pct, officer_title, tenb5_1, shares_owned_after
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("acc-dirty", "HEI, HEI.A", "2024-01-04", "2024-01-04", "P",
         1000, 12.5, "Jane Doe", 0, 0, 1, 0, 0, "", 0, 5000),
    )
    store._conn.execute(
        """
        INSERT INTO filings (
          accession, symbol, filed, trans_date, code, shares, price,
          owner_name, is_ceo, is_cfo, is_officer, is_director,
          is_ten_pct, officer_title, tenb5_1, shares_owned_after
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("acc-garbage", "N/A", "2024-01-05", "2024-01-05", "P",
         100, 5.0, "No Symbol Owner", 0, 1, 0, 0, 0, "", 0, 200),
    )
    store._conn.commit()

    rows = store.filings()
    assert len(rows) == 1
    assert rows[0].accession == "acc-dirty"
    assert rows[0].symbol == "HEI"
