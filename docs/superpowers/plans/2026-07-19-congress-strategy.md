# Congress Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `kind="congress"` strategy (STOCK Act disclosures) reusing the insider engine end-to-end; three canonical variants on the Lab leaderboard.

**Architecture:** New `momentum_desk/congress/` package mirroring `momentum_desk/insider/` and reusing its `InsiderEvent`, `run_insider` simulator, `PolygonDaily`/`SyntheticDaily`, `enrich_events`, and Lab seam. Spec: `docs/superpowers/specs/2026-07-19-congress-strategy-design.md` — READ IT FIRST; it fixes the data source, schema, filters, and variants.

**Tech Stack:** Python 3.11+ stdlib only, pytest, ruff. No new dependencies.

## Global Constraints

- Branch `feat/congress-strategy` (stacked on `feat/insider-strategy` — the insider package is present; MIRROR its conventions file-for-file).
- Trigger = filing (disclosure) date; entry next trading day's open, strictly after; `min_filed` floor like insider (window start − 5 days).
- Purchases only; drop rows: owner "DC"/dependent, non-stock asset types (funds/ETFs/options), `amount_range_low` < cfg.min_amount, `days_to_file` > cfg.max_days_to_file, unnormalizable tickers (use `insider.edgar.normalize_symbol`).
- HTTP: descriptive UA `momentum-desk marcandre.vigneault.96@gmail.com`, sequential fetches, raw JSON cached under `data/cache/congress/`.
- Every module: `from __future__ import annotations` + "why" docstring; `ruff check .` + full `pytest` green before each commit; commits end with the `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` trailer.
- Unknown/invalid config strings raise ValueError in `run_congress_strategy` (the server's existing ValueError→400 handler covers it — verify, don't duplicate).

## File Structure

```
momentum_desk/congress/
  __init__.py
  loader.py      # kadoa fetch → data/congress.db (CongressStore)
  signals.py     # CongressConfig + build_events -> list[InsiderEvent]
  power.py       # power-list load/validate; power.json data file
  power.json     # curated leadership/chairs, 118th-119th Congress, kadoa filer_id slugs
  bundles.py     # SyntheticCongressBundle, RealCongressBundle, run_congress_strategy
tests/
  test_congress_loader.py
  test_congress_signals.py
  test_congress_strategy.py   # bundles + dispatch + lab wiring + API
momentum_desk/edge/strategy.py  # congress field + dispatch (modify)
momentum_desk/edge/lab.py       # factory branch + CANONICAL (modify)
web/src/pages/LabPage.tsx       # badge generalization (modify)
.github/workflows/ci.yml        # smoke line (modify)
docs/EDGE_PLATFORM.md, FEATURES.md
```

---

### Task 1: Loader + store (`loader.py`)

**Files:** Create `momentum_desk/congress/__init__.py`, `momentum_desk/congress/loader.py`; Test `tests/test_congress_loader.py`.

**Interfaces (produces):**

```python
CONGRESS_UA = "momentum-desk marcandre.vigneault.96@gmail.com"
FILER_INDEX_URL = "https://api.github.com/repos/kadoa-org/congress-trading-monitor/contents/public/data/filer"
FILER_RAW_URL = "https://raw.githubusercontent.com/kadoa-org/congress-trading-monitor/main/public/data/filer/{name}"

@dataclass
class CongressTrade:
    filer_id: str; member_name: str; chamber: str   # "house" | "senate" | "other"
    ticker: str; transaction_date: str; filing_date: str   # ISO
    owner: str            # "SP" | "JT" | "SELF" | "DC" | "" (normalize: missing/self-ish -> "SELF")
    asset_type: str       # raw code, e.g. "ST", "OP"
    transaction_type: str # "Purchase" | "Sale" | ... (normalize case)
    amount_low: float; amount_high: float
    is_late: bool; days_to_file: int

def parse_filer_json(raw: bytes | str) -> list[CongressTrade]:
    """One kadoa per-filer JSON -> trades. Tolerant: missing key -> default,
    never KeyError; rows without ticker/dates skipped; member_name from
    filer_id slug when absent; chamber from filer_id prefix."""

class CongressStore:
    def __init__(self, db_path: str = "data/congress.db") -> None: ...
    def refresh(self, *, fetch=None, list_fetch=None, max_filers: int | None = None) -> int:
        """Fetch filer index (GitHub contents API), then each filer JSON
        (cache raw under <db dir>/cache/congress/<name>; reuse cache when file
        younger than 24h — mtime check), parse, INSERT OR IGNORE. Returns rows
        inserted. fetch(url)->bytes and list_fetch(url)->bytes injectable.
        Logs per-year row counts after refresh (density sanity, spec caveat)."""
    def trades(self, *, start: str | None = None, end: str | None = None) -> list[CongressTrade]: ...
```

SQLite schema per the spec (PRIMARY KEY(filer_id, ticker, transaction_date, filing_date, amount_low, transaction_type, owner); `refreshes(at TEXT)` bookkeeping table). Normalize tickers via `momentum_desk.insider.edgar.normalize_symbol` (drop row if None) at BOTH write and read time (mirror insider). Dates: kadoa is ISO already — validate with `date.fromisoformat`, skip row on failure.

**Steps:** (TDD, mirror tests/test_insider_edgar.py style — in-memory JSON fixtures, injected fetch)
- [ ] Failing tests: parse both chambers from filer_id; tolerant parsing (missing owner→"SELF", missing amount→0.0); bad-date and bad-ticker rows skipped; store refresh idempotent (second refresh inserts 0); trades() date-range filter; fetch sends UA; per-year density logged.
- [ ] Implement; verify against ONE real filer fetch (network one-off, e.g. house_nancy_pelosi.json) and note real field names in the report; adjust parser if reality differs.
- [ ] Full pytest + ruff; commit `feat(congress): kadoa STOCK Act loader + SQLite store`.

---

### Task 2: Signals + power list (`signals.py`, `power.py`, `power.json`)

**Files:** Create `momentum_desk/congress/signals.py`, `power.py`, `power.json`; Test `tests/test_congress_signals.py`.

**Interfaces (produces):**

```python
@dataclass
class CongressConfig:
    min_amount: float = 15_001.0
    owners: tuple[str, ...] = ("SELF", "SP", "JT")
    power_only: bool = False
    cluster_n: int = 1
    cluster_window_days: int = 30
    max_days_to_file: int = 45
    hold_days: int = 21
    stop_pct: float = 20.0
    trail_pct: float = 25.0

def build_events(trades: list[CongressTrade], cfg: CongressConfig,
                 trading_days: list[str], *, min_filed: str | None = None,
                 power: set[str] | None = None) -> list[InsiderEvent]:
```

Rules (in order): transaction_type == "Purchase"; asset stock-only (`asset_type` in {"ST", "st", "Stock", ""} — verify real codes from Task 1's report and encode what kadoa actually uses; unknown codes are DROPPED and the accepted set is a module constant); owner in cfg.owners; amount_low >= cfg.min_amount; days_to_file <= cfg.max_days_to_file; power filter when cfg.power_only (filer_id in power set — power=None + power_only → ValueError). Cluster: greedy per ticker on FILING dates within cluster_window_days, distinct filer_id count >= cluster_n. Emit `InsiderEvent(symbol, trigger_day=first trading day strictly after latest filing_date, total_value=Σ amount midpoints, n_insiders=distinct members, top_role="power" if any clustered filer in power set else "member", conviction=0.5 fixed)`. min_filed floor identical to insider (drop clusters whose latest filing_date < min_filed). Reuse `bisect` pattern from `insider/signals.py` — COPY the small trigger helper, do not import private functions.

`power.py`: `load_power(path=...) -> set[str]` validating `power.json` schema `{"congresses": {"118": {"house": [...filer_ids...], "senate": [...]}, "119": {...}}, "sources": [...]}` → flat set. `power.json`: curate via web research — Speaker, House/Senate majority+minority leaders and whips, chairs AND ranking members of: Appropriations, Armed Services, Banking/Financial Services, Commerce, Energy, Finance/Ways & Means, Foreign Affairs/Relations, HELP/Education, Intelligence, Judiciary — both congresses, names converted to kadoa slug form (`house_first_last`, lowercase, underscores; verify slug format against Task 1's real filer index in the repo's cache or the report). ~60–90 entries; include `sources` URLs. Best-effort accuracy; reviewer spot-checks 5 names.

**Steps:**
- [ ] Failing tests: each filter rule; cluster window on filing dates; distinct-member count; trigger strictly-after; min_filed floor; power_only with and without power set (ValueError); top_role assignment; deterministic ordering. Power.json: schema validation test + `load_power` returns non-empty set containing e.g. the current Speaker's slug.
- [ ] Implement + curate power.json (web research; cite sources in the file).
- [ ] Full pytest + ruff; commit `feat(congress): signal construction + curated power list`.

---

### Task 3: Bundles + wiring + smoke

**Files:** Create `momentum_desk/congress/bundles.py`; Modify `momentum_desk/edge/strategy.py`, `momentum_desk/edge/lab.py`, `.github/workflows/ci.yml`; Test `tests/test_congress_strategy.py`.

Mirror `momentum_desk/insider/bundles.py` closely:

```python
class SyntheticCongressBundle:   # days:int=252, seed:int=7; hashlib seeding, NEVER hash()
    # fabricate CongressTrade rows over SyntheticDaily symbols/days (~1 purchase
    # per symbol per 2-3 weeks, amounts 15k-500k ranges, some 2-member clusters,
    # a sprinkle of power members from a tiny built-in fake power set) ->
    # REAL build_events; density: default config at days=60 yields >=1 trade.
class RealCongressBundle:        # CongressStore.refresh() + PolygonDaily + insider enrich_events;
                                 # min_filed = window start - 5 days; power=load_power()
def run_congress_strategy(strategy, bundle, risk_cfg) -> InsiderResult:
    # CongressConfig from strategy.congress (known-field filter); validate
    # owners entries in {"SELF","SP","JT","DC"} and cluster_n >= 1 else ValueError
```

- `strategy.py`: field `congress: dict = field(default_factory=dict)` (same from_dict handling as `insider`); dispatch `kind == "congress"` via late import, `provider_factory("congress")`.
- `lab.py`: factory branches for `session == "congress"` (synthetic/real); CANONICAL += the spec's 3 variants (exact names: "Congress: member buys", "Congress: power buys", "Congress: cluster buys").
- CI smoke line mirroring the insider one (SyntheticCongressBundle, days=60, assert trades > 0); run locally.

**Steps:**
- [ ] Failing tests: round-trip with congress field; dispatch returns AccountRun; synthetic determinism (two calls identical); density at days=60; canonical names present; gauntlet_key None; run_only synthetic end-to-end (LAB_SEED=off, env keys cleared); API create+run 200 and bad owners → 400; seed backfill adds congress names to a pre-populated store.
- [ ] Implement; full pytest + ruff; run smoke line locally; commit `feat(congress): kind="congress" — bundles, Lab dispatch, canonical variants, CI smoke`.

---

### Task 4: UI badge + docs

**Files:** Modify `web/src/pages/LabPage.tsx`; `docs/EDGE_PLATFORM.md`; `FEATURES.md`.

- Generalize the existing insider pill: render a badge for `r.kind === "insider"` (amber, unchanged) OR `r.kind === "congress"` (violet `#8a6fc4`-family tone matching the file's palette), same idiom — a tiny map, not two copy-pasted blocks.
- `docs/EDGE_PLATFORM.md`: "Congress strategies (event-driven)" subsection — data source, the three variants WITH EXACT SHIPPED NAMES/filters, the honest-evidence framing (research-tier, aggregate alpha ≈ 0 post-2012, power/short-side conditionals), pointer to the spec. `FEATURES.md`: one shipped line.
- [ ] `cd web && npm run build` clean; full pytest + ruff; verify doc variant names against lab.py CANONICAL before committing (the insider Task 8 failed review on exactly this); commit `feat(congress): Lab UI badge + docs`.

---

## Verification (after all tasks)

1. `ruff check .` + full `pytest` + `cd web && npm run build` + both CI smoke lines locally.
2. End-to-end synthetic via API: create + run each congress variant, badge renders.
3. Real-data shakedown (network, controller-driven): `CongressStore().refresh()` then 1y run of "Congress: member buys" — confirm no crashes, plausible trigger dates (filing-date-based), log per-year densities.
