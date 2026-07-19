# Insider-Filing Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `kind="insider"` strategy type — SEC Form 4 insider-purchase signals, multi-day daily-bar simulation — runnable in the Lab and ranked on the existing leaderboard.

**Architecture:** A parallel event-strategy engine in a new `momentum_desk/insider/` package. It plugs in at the `Strategy` → `run_strategy` → `AccountRun` seam: same `compute_metrics`, same `LabStore`, same leaderboard. It does NOT touch `edge/screen.py`, `edge/portfolio.py`, or the live engine. Spec: `docs/superpowers/specs/2026-07-19-insider-strategy-design.md`.

**Tech Stack:** Python 3.11+ stdlib only (urllib, sqlite3, zipfile, csv), pytest, ruff. No new dependencies.

## Global Constraints

- No new pip dependencies. Stdlib + existing repo modules only.
- All SEC HTTP requests MUST send header `User-Agent: momentum-desk marcandre.vigneault.96@gmail.com` (SEC blocks without it) and stay far below 10 req/s.
- Point-in-time discipline: the tradable event is the **filing date**; entry is the next trading day's open, strictly after the filing date. Never key off transaction date.
- Only transaction code `P` (open-market purchase) is a signal. `A`, `M`, `F`, `G`, `S` etc. are never signals.
- All new files: `from __future__ import annotations`, module docstring explaining "why" (match repo style), dataclasses over dicts at boundaries.
- Run `ruff check .` and the full `pytest` before every commit. Test env: set `LAB_SEED=off` where the Lab store is involved (existing convention).
- Commit after each task with a `feat(insider): ...` message ending in the repo's Co-Authored-By trailer.

## File Structure

```
momentum_desk/insider/
  __init__.py        # empty package marker
  models.py          # InsiderFiling, InsiderEvent, InsiderConfig, DailyBar, InsiderBundle protocol
  signals.py         # build_events() — pure signal construction (filters, cluster, routine)
  edgar.py           # SEC quarterly form345.zip download → parse → data/insider.db (SQLite)
  prices.py          # SyntheticDaily + PolygonDaily daily-bar providers
  enrich.py          # market cap / sector / news enrichment (cached, None-tolerant)
  simulate.py        # run_insider() — daily-bar multi-day portfolio sim → AccountRun
  bundles.py         # SyntheticInsiderBundle, RealInsiderBundle (wire edgar+prices+enrich)
tests/
  test_insider_signals.py
  test_insider_edgar.py
  test_insider_prices.py
  test_insider_simulate.py
  test_insider_strategy.py   # Strategy round-trip + dispatch + lab wiring
web/src/pages/LabPage.tsx    # small "insider" badge (modify)
momentum_desk/edge/strategy.py  # insider field + dispatch (modify)
momentum_desk/edge/lab.py       # provider factory + CANONICAL variants (modify)
.github/workflows/ci.yml        # smoke line (modify)
```

---

### Task 1: Models + signal construction (`models.py`, `signals.py`)

**Files:**
- Create: `momentum_desk/insider/__init__.py` (empty)
- Create: `momentum_desk/insider/models.py`
- Create: `momentum_desk/insider/signals.py`
- Test: `tests/test_insider_signals.py`

**Interfaces:**
- Produces (later tasks rely on these exact names):

```python
# models.py
@dataclass
class InsiderFiling:
    accession: str
    symbol: str
    filed: str            # YYYY-MM-DD (EDGAR filing date)
    trans_date: str       # YYYY-MM-DD
    code: str             # SEC transaction code: P, S, A, M, F, G, ...
    shares: float
    price: float
    owner_name: str
    is_ceo: bool = False
    is_cfo: bool = False
    is_officer: bool = False
    is_director: bool = False
    is_ten_pct: bool = False
    officer_title: str = ""
    tenb5_1: bool = False          # 10b5-1 plan flag (False when unknown/pre-2023)
    shares_owned_after: float = 0.0

    @property
    def value(self) -> float:      # dollar value of the transaction
        return self.shares * self.price

@dataclass
class InsiderEvent:
    symbol: str
    trigger_day: str               # first tradable day (next trading day after max filed date)
    total_value: float
    n_insiders: int
    top_role: str                  # "ceo" | "cfo" | "officer" | "director" | "10pct"
    conviction: float              # total bought value / (value + holdings-after value), 0..1
    market_cap: float | None = None
    sector: str | None = None
    has_recent_news: bool = False
    news_headline: str = ""

@dataclass
class InsiderConfig:
    min_value: float = 25_000.0
    roles: str = "officer"         # "any" | "officer" (any officer or director) | "ceo_cfo"
    cluster_n: int = 1             # min distinct insiders buying within the window
    cluster_window_days: int = 10  # calendar-day window for clustering
    exclude_10b51: bool = True
    exclude_routine: bool = True   # Cohen-Malloy-Pomorski same-calendar-month heuristic
    max_market_cap: float | None = None   # dollars; None = no cap; None market_cap passes unless include_unknown=False
    include_unknown_cap: bool = True
    news_filter: str = "any"       # "any" | "quiet" (no news in lookback) | "with_news"
    news_lookback_days: int = 5
    hold_days: int = 20            # time stop, trading days
    stop_pct: float = 20.0         # hard stop below entry
    trail_pct: float = 15.0        # trailing stop from highest close since entry
```

```python
# signals.py
def routine_keys(filings: list[InsiderFiling]) -> set[tuple[str, str]]:
    """(owner_name, symbol) pairs whose code-P buys hit the same calendar month
    in >= 3 distinct years — the routine-trader filter."""

def build_events(filings: list[InsiderFiling], cfg: InsiderConfig,
                 trading_days: list[str]) -> list[InsiderEvent]:
    """Pure function: apply filters, cluster per symbol, emit events sorted by
    trigger_day. Only days in `trading_days` are eligible trigger days; the
    trigger is the first trading day STRICTLY AFTER the latest filing date in
    the cluster. Filings whose trigger would fall past the last trading day
    are dropped. Enrichment fields are left at defaults (Task 4 fills them)."""
```

- Signal rules `build_events` must implement, in order:
  1. keep only `code == "P"` and `shares > 0` and `price > 0`
  2. drop `tenb5_1` filings when `cfg.exclude_10b51`
  3. role filter: `"ceo_cfo"` → `is_ceo or is_cfo` (derive from `officer_title` case-insensitive containing "chief executive"/"ceo" or "chief financial"/"cfo" — set in the parser, Task 2; here just trust the flags); `"officer"` → `is_officer or is_director`; `"any"` → all except pure `is_ten_pct` with no other role
  4. drop routine keys when `cfg.exclude_routine` (compute from the FULL filings list passed in, before other filters, so history informs the classification)
  5. group remaining filings by symbol; slide the cluster window: a cluster is the set of filings within `cluster_window_days` calendar days ending at each filing's `filed` date; emit an event when distinct `owner_name` count ≥ `cluster_n` AND cluster total value ≥ `cfg.min_value`; consume the clustered filings (one event per cluster, no overlapping re-emission of the same filings)
  6. `top_role` priority: ceo > cfo > officer > director > 10pct across the cluster
  7. `conviction = total_value / (total_value + Σ(shares_owned_after × price))`, guarded: if denominator ≤ 0 → 1.0

**Steps:**

- [ ] **Step 1: Write failing tests** — `tests/test_insider_signals.py` with a `mk(...)` filing factory helper. Cover at minimum:

```python
def mk(sym="ACME", filed="2025-03-10", code="P", shares=1000, price=50.0,
       owner="Jane Doe", **kw) -> InsiderFiling: ...

DAYS = ["2025-03-10", "2025-03-11", "2025-03-12", "2025-03-13", "2025-03-14",
        "2025-03-17", "2025-03-18", "2025-03-19", "2025-03-20", "2025-03-21"]

def test_only_code_p_counts():            # S/A/M/F/G filings produce no event
def test_min_value_filter():              # 100sh @ $10 < $25k → no event
def test_trigger_is_next_trading_day():   # filed Fri 03-14 → trigger Mon 03-17
def test_filed_on_last_day_dropped():     # no future trading day → no event
def test_cluster_requires_distinct_insiders():  # cluster_n=2, same owner twice → no event
def test_cluster_two_insiders_within_window():  # 2 owners 5 days apart, cluster_n=2 → 1 event, n_insiders=2
def test_cluster_outside_window_no_merge():     # 2 owners 15 days apart, window=10 → separate solo events (cluster_n=1)
def test_role_filter_ceo_cfo():           # director-only filing filtered out under "ceo_cfo"
def test_ten_pct_only_excluded_under_any():
def test_10b51_excluded_by_default():
def test_routine_keys_same_month_three_years():  # buys in March 2022/23/24 → key present; 2 years → absent
def test_routine_filings_dropped():
def test_top_role_priority_and_conviction():
def test_events_sorted_by_trigger_day():
```

- [ ] **Step 2: Run to verify failure** — `pytest tests/test_insider_signals.py -v` → import errors.
- [ ] **Step 3: Implement `models.py` then `signals.py`** exactly per the interfaces above. Cluster algorithm: per symbol, sort filings by `filed`; greedy left-to-right — start a cluster at the first unconsumed filing, extend with filings whose `filed` ≤ start + `cluster_window_days` calendar days, emit if thresholds met (else the filings remain consumed as a failed cluster only when below `cluster_n`; a solo below `min_value` is simply dropped). Use `datetime.date.fromisoformat` for date math; `bisect` over `trading_days` for the trigger lookup.
- [ ] **Step 4: Run to verify pass** — `pytest tests/test_insider_signals.py -v` → all PASS. `ruff check .` clean.
- [ ] **Step 5: Commit** — `feat(insider): signal models + event construction (cluster, routine, role filters)`

---

### Task 2: EDGAR bulk loader (`edgar.py`)

**Files:**
- Create: `momentum_desk/insider/edgar.py`
- Test: `tests/test_insider_edgar.py` (+ small inline fixture strings, no binary fixtures)

**Interfaces:**
- Consumes: `InsiderFiling` from Task 1.
- Produces:

```python
SEC_UA = "momentum-desk marcandre.vigneault.96@gmail.com"
ZIP_URL_PATTERNS = [
    "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{yq}_form345.zip",
    "https://www.sec.gov/files/datastandardsinnovation/data/insider-transactions-data-sets/{yq}_form345.zip",
]   # {yq} like "2025q1"; try in order, first 200 wins

def parse_quarter_zip(zip_bytes: bytes) -> list[InsiderFiling]:
    """Parse SUBMISSION.tsv + REPORTINGOWNER.tsv + NONDERIV_TRANS.tsv from one
    quarterly ZIP into InsiderFiling rows (one row per non-derivative
    transaction line, joined on ACCESSION_NUMBER)."""

class EdgarStore:
    def __init__(self, db_path: str = "data/insider.db") -> None: ...
    def load_quarter(self, year: int, quarter: int, *, fetch=None) -> int:
        """Idempotent: skips if the quarter is already recorded. Downloads via
        urllib with SEC_UA, caches the raw zip at data/cache/edgar/{yq}.zip,
        parses, inserts. Returns rows inserted. `fetch(url) -> bytes` injectable
        for tests."""
    def load_range(self, start_year: int, *, fetch=None) -> None: ...
    def filings(self, *, start: str | None = None, end: str | None = None) -> list[InsiderFiling]: ...
```

- SQLite schema (created on init):

```sql
CREATE TABLE IF NOT EXISTS filings (
  accession TEXT, symbol TEXT, filed TEXT, trans_date TEXT, code TEXT,
  shares REAL, price REAL, owner_name TEXT,
  is_ceo INTEGER, is_cfo INTEGER, is_officer INTEGER, is_director INTEGER,
  is_ten_pct INTEGER, officer_title TEXT, tenb5_1 INTEGER, shares_owned_after REAL,
  PRIMARY KEY (accession, owner_name, trans_date, code, shares, price)
);
CREATE INDEX IF NOT EXISTS idx_filings_filed ON filings(filed);
CREATE TABLE IF NOT EXISTS quarters_loaded (yq TEXT PRIMARY KEY);
```

- **Column-name caution:** the TSV headers below are the expected names — VERIFY against one real quarter before finalizing (implementer: `curl -s -H "User-Agent: momentum-desk marcandre.vigneault.96@gmail.com" https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/2024q1_form345.zip -o /tmp/q.zip && unzip -l /tmp/q.zip && unzip -p /tmp/q.zip NONDERIV_TRANS.tsv | head -2` — adjust parser to the real headers if they differ, and make the parser tolerant: missing column → default, never KeyError):
  - `SUBMISSION.tsv`: `ACCESSION_NUMBER`, `FILING_DATE`, `ISSUERTRADINGSYMBOL`, `DOCUMENT_TYPE`
  - `REPORTINGOWNER.tsv`: `ACCESSION_NUMBER`, `RPTOWNERNAME`, `RPTOWNER_RELATIONSHIP` flags — expected as `ISDIRECTOR`, `ISOFFICER`, `ISTENPERCENTOWNER`, `OFFICERTITLE`
  - `NONDERIV_TRANS.tsv`: `ACCESSION_NUMBER`, `TRANS_DATE`, `TRANS_CODE`, `TRANS_SHARES`, `TRANS_PRICEPERSHARE`, `SHRS_OWND_FOLWNG_TRANS`
  - 10b5-1 flag: look for a column containing `10B5` in SUBMISSION or NONDERIV_TRANS (post-2023 only); absent → `tenb5_1=False`
  - `FILING_DATE` may be `DD-MON-YYYY` (e.g. `04-JAN-2024`) — normalize to ISO `YYYY-MM-DD`; handle both formats.
  - CEO/CFO derivation: `officer_title` lower-cased contains `"chief executive"` or equals/contains `"ceo"` → `is_ceo`; `"chief financial"`/`"cfo"` → `is_cfo`.
  - Rows with blank/missing symbol, non-positive shares or price, or `DOCUMENT_TYPE` not `4`/`4/A` are skipped (keep only Form 4).

**Steps:**

- [ ] **Step 1: Write failing tests** — build an in-memory ZIP with `zipfile` + `io.BytesIO` containing three tiny TSVs (tab-separated, header + 3 rows: one CEO P-buy, one director S-sale, one owner with blank symbol). Tests:

```python
def test_parse_quarter_zip_joins_and_normalizes():   # exact InsiderFiling fields incl. is_ceo from title, ISO date
def test_parse_skips_blank_symbol_and_non_form4():
def test_store_load_quarter_idempotent(tmp_path):    # inject fetch=lambda url: zip_bytes; second load inserts 0
def test_store_filings_date_range(tmp_path):
def test_fetch_sends_user_agent(monkeypatch):        # assert Request has SEC_UA header
```

- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.** Use `csv.DictReader(io.TextIOWrapper(zf.open(name), encoding="latin-1"), delimiter="\t")`. Insert with `INSERT OR IGNORE`. Download with `urllib.request.Request(url, headers={"User-Agent": SEC_UA})`, try each `ZIP_URL_PATTERNS` entry, cache raw bytes to `data/cache/edgar/{yq}.zip` and reuse when present.
- [ ] **Step 4: Verify against ONE real quarter** (network, one-off, not a pytest): run the curl above, then `python -c "from momentum_desk.insider.edgar import parse_quarter_zip; rows = parse_quarter_zip(open('/tmp/q.zip','rb').read()); print(len(rows), rows[0])"`. Fix headers if reality differs, then re-run unit tests.
- [ ] **Step 5: Run full suite + ruff, commit** — `feat(insider): EDGAR form345 bulk loader + SQLite store`

---

### Task 3: Daily prices (`prices.py`)

**Files:**
- Create: `momentum_desk/insider/prices.py`
- Test: `tests/test_insider_prices.py`

**Interfaces:**
- Consumes: `CachedClient` from `momentum_desk/backtest/client.py` (existing).
- Produces:

```python
@dataclass
class DailyBar:
    day: str    # YYYY-MM-DD
    o: float
    h: float
    l: float
    c: float
    v: int

class DailyProvider(Protocol):
    name: str
    def trading_days(self) -> list[str]: ...
    def daily(self, symbol: str) -> list[DailyBar]: ...   # ascending by day, [] if unknown symbol

class SyntheticDaily:
    """Deterministic (seeded per symbol) random-walk daily bars over N weekdays
    ending 2026-07-17. name = "synthetic"."""
    def __init__(self, days: int = 252, seed: int = 7) -> None: ...

class PolygonDaily:
    """Daily aggregates via /v2/aggs/ticker/{sym}/range/1/day/{frm}/{to},
    one cached call per symbol. trading_days() from a reference symbol (SPY).
    name = "polygon". Cache dir data/cache/polygon (same as PolygonHistory)."""
    def __init__(self, api_key: str, days: int = 252, max_per_min: float = 5,
                 client: CachedClient | None = None) -> None: ...
```

- `SyntheticDaily` determinism: `random.Random(hash((symbol, seed)) & 0xFFFF)`-style seeding; prices ~ $5–$80 start, ±3% daily moves, OHLC self-consistent (`l ≤ o,c ≤ h`). Same symbol+seed → identical bars (assert in test).
- `PolygonDaily.daily` parses `{"results": [{"t": ms_epoch, "o":…, "h":…, "l":…, "c":…, "v":…}]}` → `DailyBar` with `day` from UTC epoch. Missing/error → `[]` (catch `urllib.error.HTTPError`).

**Steps:**

- [ ] **Step 1: Failing tests:** determinism, OHLC consistency, weekday-only days, `PolygonDaily` with injected `CachedClient(fetch=fake)` returns parsed bars and `[]` on 404.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run to verify pass + ruff.**
- [ ] **Step 5: Commit** — `feat(insider): daily-bar providers (synthetic + polygon aggs)`

---

### Task 4: Enrichment (`enrich.py`)

**Files:**
- Create: `momentum_desk/insider/enrich.py`
- Test: `tests/test_insider_signals.py` (append) or new `tests/test_insider_enrich.py`

**Interfaces:**
- Consumes: `InsiderEvent`, `InsiderConfig`, `CachedClient`.
- Produces:

```python
def enrich_events(events: list[InsiderEvent], client: CachedClient | None) -> list[InsiderEvent]:
    """Fill market_cap/sector (polygon /v3/reference/tickers/{sym}: results.market_cap,
    results.sic_description) and has_recent_news/news_headline (/v2/reference/news,
    published_utc within [trigger_day - lookback, trigger_day)). client None → no-op.
    Any per-symbol failure → fields stay None/False. Never raises."""

def filter_events(events: list[InsiderEvent], cfg: InsiderConfig) -> list[InsiderEvent]:
    """Apply max_market_cap (None cap passes iff include_unknown_cap) and
    news_filter ("quiet" → not has_recent_news, "with_news" → has_recent_news)."""
```

- News lookback uses `cfg.news_lookback_days` — pass cfg into `enrich_events(events, client, cfg)` (adjust signature accordingly; keep both functions pure/injectable).

**Steps:**

- [ ] **Step 1: Failing tests** with fake `CachedClient(fetch=...)`: market cap + sector filled; news inside window sets flag, news ON trigger day does NOT (lookahead guard); HTTP error → defaults; `filter_events` matrix: small-cap pass/fail, unknown cap with `include_unknown_cap` both ways, quiet/with_news.
- [ ] **Step 2–4: Fail → implement → pass + ruff.**
- [ ] **Step 5: Commit** — `feat(insider): event enrichment (market cap, sector, news) + conditioning filters`

---

### Task 5: Multi-day simulator (`simulate.py`)

**Files:**
- Create: `momentum_desk/insider/simulate.py`
- Test: `tests/test_insider_simulate.py`

**Interfaces:**
- Consumes: `InsiderEvent`, `InsiderConfig`, `DailyProvider`, `RiskConfig` (existing, `momentum_desk/risk.py`), `compute_metrics` + `Trade` (existing), `AccountRun`.
- Produces:

```python
@dataclass
class InsiderResult(AccountRun):
    config: dict = field(default_factory=dict)   # the InsiderConfig used

@dataclass
class InsiderTrade:
    day: str          # entry day (keeps LabPage/date-scrubber contract)
    exit_day: str
    symbol: str
    entry_tod: int    # constant 570 (open) — keeps SimTrade-shaped consumers happy
    exit_tod: int     # constant 960 (close)
    entry: float
    exit: float
    shares: int
    pnl: float
    r_multiple: float
    exit_reason: str  # "stop" | "trail" | "time" | "end"
    hold_days: int

def run_insider(events: list[InsiderEvent], provider: DailyProvider,
                cfg: InsiderConfig, risk_cfg: RiskConfig,
                *, max_concurrent: int = 5, max_gross_pct: float = 100.0,
                slippage_pct: float = 0.3,
                commission_per_share: float = 0.005, commission_min: float = 1.0) -> InsiderResult:
```

- Simulation semantics (must match tests exactly):
  - Day loop over `provider.trading_days()`. Events indexed by `trigger_day`.
  - **Exits first, then entries**, each day.
  - Exit checks for an open position on day D's bar, in priority order:
    1. hard stop `stop = entry * (1 - cfg.stop_pct/100)`: if `bar.o <= stop` → exit at `bar.o` (gap-through fills at open); elif `bar.l <= stop` → exit at `stop`; reason `"stop"`.
    2. trailing stop `trail = highest_close_since_entry * (1 - cfg.trail_pct/100)` (uses closes up to the PRIOR day, then updated with today's close after the check): same open-gap logic; reason `"trail"`. Hard stop takes precedence when both hit.
    3. time stop: position has been held `cfg.hold_days` trading days (entry day = day 0) → exit at `bar.c`, reason `"time"`.
    4. last trading day of the window → exit at `bar.c`, reason `"end"`.
  - Entry on trigger day D: fill at `bar.o * (1 + slippage_pct/100)`; skip if no bars for symbol on D (count in `n_skipped_capacity`? NO — count separately as a plain skip, not capacity; just don't count it as a signal taken), skip if `len(open) >= max_concurrent` or gross cap exceeded (those DO increment `n_skipped_capacity`).
  - Sizing: `risk_dollars = equity_base * risk_pct/100` where `equity_base` is starting equity for `mode="fixed"`-style (RiskConfig.compound False) or current equity when compound; `shares = int(risk_dollars / (entry - stop))`; also cap `shares` so `shares*entry <= equity * 0.25` (per-name notional cap, mirroring live risk defaults). `shares <= 0` → skip.
  - Commission `2.0 * max(commission_min, shares * commission_per_share)` (round turn, same as `edge/portfolio.py`).
  - PnL realized on exit day; `r_multiple = pnl / risk_dollars`.
  - `equity_curve`: append realized equity after every exit. `daily_equity`: per day, cash equity + open positions marked at that day's close. `monthly`: reuse the exact `_monthly` grouping logic shape from `edge/portfolio.py` (group by `day[:7]` of ENTRY day) — copy the small helper, don't import the private.
  - Metrics: map `InsiderTrade` → backtest `Trade` (like `edge/portfolio.py:183-186`) and call `compute_metrics(bt_trades, curve, start)`.
  - `n_signals` = events whose trigger day is in the window and symbol has bars; `n_taken` = entered.

**Steps:**

- [ ] **Step 1: Failing tests** using a hand-built `FakeDaily` provider (explicit bars dict) so every fill is arithmetic-checkable:

```python
def test_entry_next_open_with_slippage():
def test_hard_stop_intraday_fill():          # low pierces stop → exit exactly at stop
def test_hard_stop_gap_through_fills_open(): # open below stop → exit at open
def test_trailing_stop_from_high_close():
def test_time_stop_after_hold_days():
def test_end_of_window_close():
def test_max_concurrent_skips_and_counts():
def test_compound_vs_fixed_sizing():
def test_daily_equity_marks_open_positions():
def test_result_is_account_run_with_metrics():   # isinstance(r, AccountRun); metrics["trades"] correct
```

- [ ] **Step 2–4: Fail → implement → pass + ruff + full pytest.**
- [ ] **Step 5: Commit** — `feat(insider): multi-day daily-bar portfolio simulator`

---

### Task 6: Bundles + Strategy/Lab integration (`bundles.py`, `strategy.py`, `lab.py`)

**Files:**
- Create: `momentum_desk/insider/bundles.py`
- Modify: `momentum_desk/edge/strategy.py` (add field + dispatch)
- Modify: `momentum_desk/edge/lab.py` (provider factory + CANONICAL)
- Test: `tests/test_insider_strategy.py`

**Interfaces:**

```python
# bundles.py
class InsiderBundle(Protocol):
    name: str
    def events(self, cfg: InsiderConfig) -> list[InsiderEvent]: ...
    def provider(self) -> DailyProvider: ...

class SyntheticInsiderBundle:
    """SyntheticDaily prices + deterministically fabricated filings (~1 CEO/officer
    P-buy per ~5 symbols/week, some clustered, seeded) run through the REAL
    build_events/filter pipeline. name="synthetic"."""
    def __init__(self, days: int = 252, seed: int = 7) -> None: ...

class RealInsiderBundle:
    """EdgarStore filings (loading quarters on demand for the window + 3y routine
    lookback) + PolygonDaily + enrich_events with the polygon CachedClient.
    name="polygon"."""
    def __init__(self, api_key: str, days: int = 252) -> None: ...

def run_insider_strategy(strategy: "Strategy", bundle: InsiderBundle,
                         risk_cfg: RiskConfig) -> InsiderResult:
    """cfg = InsiderConfig(**{k: v for k, v in strategy.insider.items()
                              if k in InsiderConfig.__dataclass_fields__});
    events = bundle.events(cfg)  (bundle applies enrich+filter internally);
    return run_insider(events, bundle.provider(), cfg, risk_cfg,
                       max_concurrent=strategy.max_concurrent,
                       max_gross_pct=strategy.max_gross_pct,
                       slippage_pct=strategy.slippage_pct)"""
```

- `edge/strategy.py` changes (exact):
  - `Strategy` gains field `insider: dict = field(default_factory=dict)` (after `legs`). `from_dict`: add `"insider"` to the excluded-known set alongside sizing/legs and set `strat.insider = d_insider if isinstance(d_insider, dict) else {}`.
  - `run_strategy`: before the combo branch add:

```python
    if strategy.kind == "insider":
        from ..insider.bundles import run_insider_strategy
        return run_insider_strategy(strategy, provider_factory("insider"), risk)
```

  (Late import avoids a cycle; `provider_factory("insider")` returns an `InsiderBundle` — see lab change.)
- `edge/lab.py` changes (exact):
  - `_provider_factory`: inside each returned lambda, branch first: `if session == "insider": return SyntheticInsiderBundle(days=days)` for synthetic, `RealInsiderBundle(api_key=key, days=days)` for polygon. (Import at module top.)
  - `CANONICAL` append (values straight from the spec):

```python
    Strategy(name="Insider: officer buys", kind="insider",
             insider={"roles": "officer", "min_value": 25_000.0}),
    Strategy(name="Insider: CEO/CFO buys", kind="insider",
             insider={"roles": "ceo_cfo", "min_value": 25_000.0}),
    Strategy(name="Insider: cluster buys", kind="insider",
             insider={"cluster_n": 2, "cluster_window_days": 10}),
    Strategy(name="Insider: small-cap cluster", kind="insider",
             insider={"cluster_n": 2, "max_market_cap": 2_000_000_000.0,
                      "include_unknown_cap": False}),
    Strategy(name="Insider: news-quiet buys", kind="insider",
             insider={"roles": "officer", "news_filter": "quiet"}),
```

  - `gauntlet_key` already returns `None` for `kind != "single"` — verify with a test, no change needed.

**Steps:**

- [ ] **Step 1: Failing tests:**

```python
def test_strategy_insider_round_trip():       # to_dict/from_dict preserves insider dict
def test_run_strategy_dispatches_insider():   # kind="insider" + factory returning SyntheticInsiderBundle → AccountRun with trades >= 0 and days > 0
def test_synthetic_bundle_produces_events():  # events non-empty at default config over 252 days; deterministic across two calls
def test_gauntlet_key_none_for_insider():
def test_canonical_contains_insider_variants():  # 5 names present
def test_lab_run_only_insider_synthetic(monkeypatch):  # LAB_SEED=off, no data key → run_only(insider strat) returns AccountRun with metrics
```

- [ ] **Step 2–4: Fail → implement → pass. Run FULL pytest** (existing `test_strategy.py`, `test_lab_api.py` must stay green) + ruff.
- [ ] **Step 5: Commit** — `feat(insider): Strategy kind="insider" — Lab dispatch, bundles, canonical variants`

---

### Task 7: Lab API + CI smoke

**Files:**
- Modify: `.github/workflows/ci.yml` (smoke step)
- Test: `tests/test_insider_strategy.py` (append) or `tests/test_lab_api.py` pattern

**Steps:**

- [ ] **Step 1: Failing test** (follow the existing `tests/test_lab_api.py` client fixture pattern, `LAB_SEED=off`):

```python
def test_lab_api_create_and_run_insider(client):
    r = client.post("/api/lab/strategies", json={"name": "My insider", "kind": "insider",
                                                 "insider": {"roles": "ceo_cfo"}})
    assert r.status_code == 200
    r = client.post("/api/lab/run", json={"strategy": "My insider", "window": "1y",
                                          "data_source": "synthetic"})
    assert r.status_code == 200
    body = r.json()
    assert body["result"]["metrics"]["trades"] >= 0
```

  Adjust request/response field names to what `tests/test_lab_api.py` actually uses — read it first; the assertion intent is fixed.
- [ ] **Step 2: Run to verify current behavior** — likely already passes thanks to Task 6 (the API stores `Strategy.from_dict` blobs). If it passes immediately, verify by breaking dispatch temporarily is NOT needed — just confirm the test exercises the insider path (`kind == "insider"` in the stored config).
- [ ] **Step 3: Extend the CI smoke step** in `.github/workflows/ci.yml` with one line mirroring the existing simulator smoke:

```bash
python -c "from momentum_desk.insider.bundles import SyntheticInsiderBundle, run_insider_strategy; from momentum_desk.edge.strategy import Strategy; from momentum_desk.risk import RiskConfig; r = run_insider_strategy(Strategy(name='smoke', kind='insider'), SyntheticInsiderBundle(days=60), RiskConfig()); assert r.metrics['trades'] > 0, r.metrics"
```

  Tune `SyntheticInsiderBundle` event density so 60 days reliably yields ≥1 trade (adjust the fabrication rate in Task 6 if needed).
- [ ] **Step 4: Run the smoke line locally; full pytest + ruff.**
- [ ] **Step 5: Commit** — `feat(insider): lab API coverage + CI smoke for insider strategy`

---

### Task 8: Lab UI badge + docs

**Files:**
- Modify: `web/src/pages/LabPage.tsx` (leaderboard row: small "insider" badge when the run/strategy `kind === "insider"`, styled like existing inline badges/pills in that file — read the file first and reuse its patterns; if the leaderboard row data lacks `kind`, add it to the API response the same way existing fields flow through `web/src/api.ts`)
- Modify: `docs/EDGE_PLATFORM.md` — add a short "Insider strategies (event-driven)" subsection: what it is, the five canonical variants, pointer to the spec doc.
- Modify: `FEATURES.md` — one roadmap line marked shipped.

**Steps:**

- [ ] **Step 1: Read `LabPage.tsx` + `api.ts`, add the badge + (if needed) the `kind` field end-to-end.**
- [ ] **Step 2: `cd web && npm run build`** — must compile clean.
- [ ] **Step 3: Docs edits.**
- [ ] **Step 4: Full pytest + ruff one last time.**
- [ ] **Step 5: Commit** — `feat(insider): Lab UI badge + docs`

---

## Verification (after all tasks)

1. `ruff check .` — clean.
2. `pytest` — all green, including every pre-existing test.
3. CI smoke lines run locally.
4. `cd web && npm run build` — clean.
5. End-to-end synthetic run via the API (uvicorn + curl, `LAB_SEED=off`): create + run an insider strategy, confirm the leaderboard row appears with the badge.
6. OPTIONAL (needs POLYGON_API_KEY + network): `EdgarStore().load_quarter(2024, 1)` + a real 1y `run_only` on "Insider: CEO/CFO buys" — sanity-check trades look plausible (entries next-day after filings, multi-day holds). Not required for the PR; real seed runs are a follow-up.
