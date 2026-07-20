# Congress-Trading Strategy — Design

**Date:** 2026-07-19
**Status:** Approved for implementation
**Scope:** `kind="congress"` — event-driven strategy from STOCK Act periodic transaction reports, reusing the insider engine (daily-bar simulator, prices, enrichment, Lab seam). Research-tier: the evidence says aggregate copy-congress alpha is ~zero post-2012; this exists to test the surviving conditional signals against our own bar, expecting possible (useful) failure.

## Evidence-derived rules (from the 2026-07-19 probe)

- Trigger on **filing (disclosure) date**, never transaction date (up-to-45-day lag; trading on transaction date is lookahead).
- Post-disclosure drift horizon ≈ 1 month → default hold 21 trading days.
- **Member power** (leadership/chairs) is the one robust conditioning positive; committee-sector overlap is empirically rejected — not built.
- Size floor: drop the modal $1,001–15,000 bracket (min `amount_range_low` ≥ 15,001).
- Keep spouse (SP) and joint (JT) trades; exclude dependents (DC).
- Single-stock assets only (no funds/ETFs). Options rows excluded in v1 (long-stock simulator).
- Skip filings with `days_to_file` > 45 (stale + late-filer noise).
- Long side only in v1 (simulator is long-only); the literature's short-side signal is a Phase 2 item.

## Data source (verified in the probe)

Primary: **kadoa-org/congress-trading-monitor** (GitHub, MIT, daily-refresh): per-filer JSON files at
`https://raw.githubusercontent.com/kadoa-org/congress-trading-monitor/main/public/data/filer/{filer}.json`,
directory listing via the GitHub contents API (`https://api.github.com/repos/kadoa-org/congress-trading-monitor/contents/public/data/filer`). ~54k transactions 2012→present, House+Senate, fields incl. `transaction_date`, `filing_date`, `owner` (SP/JT/self), `ticker`, `asset_type`, `transaction_type`, `amount_range_low/high`, `is_late`, `days_to_file`, `filer_id`, `doc_url`. Chamber derivable from `filer_id` prefix (`house_`/`senate_`).
Fallback/cross-check (not built in v1): official House Clerk bulk index. The old house/senate-stock-watcher S3 datasets are dead — do not reference them.
Caveats recorded: dataset is a vendor showcase (validate row density per year at load time and log it); amendments make true point-in-time reconstruction approximate; 5 U.S.C. §13107 restricts commercial use of disclosure data (personal research/trading use).

## Architecture

New package `momentum_desk/congress/`, mirroring `momentum_desk/insider/` and REUSING its engine:

- **`loader.py`** — fetch filer list + per-filer JSONs (SEC-style politeness: descriptive UA, sequential; raw JSON cached under `data/cache/congress/`), normalize into `data/congress.db` (SQLite): table `trades(filer_id, member_name, chamber, ticker, transaction_date, filing_date, owner, asset_type, transaction_type, amount_low, amount_high, is_late, days_to_file, PRIMARY KEY(filer_id, ticker, transaction_date, filing_date, amount_low, transaction_type, owner))`. Idempotent refresh (`INSERT OR IGNORE`); symbols normalized via the existing `insider.edgar.normalize_symbol`.
- **`signals.py`** — `CongressConfig` dataclass (min_amount: float = 15_001, owners: allowed set, power_only: bool = False, cluster_n: int = 1, cluster_window_days: int = 30, max_days_to_file: int = 45, hold_days: int = 21, stop_pct: float = 20.0, trail_pct: float = 25.0) + `build_events(trades, cfg, trading_days, min_filed=None) -> list[InsiderEvent]` — reuses the insider `InsiderEvent` shape (n_insiders ↦ distinct members, top_role ↦ "power"|"member", conviction ↦ amount_low/amount_high midpoint scaled) so `insider.simulate.run_insider` runs unchanged. Purchases only; same greedy cluster algorithm as insider (window on filing dates); same strictly-after trigger + min_filed floor.
- **`power.py` + `power.json`** — curated best-effort list of House/Senate leadership and committee chairs/ranking members for the 118th–119th Congresses, keyed by kadoa `filer_id` slug; loader function with schema validation. Documented as curated data with sources.
- **`bundles.py`** — `SyntheticCongressBundle` (fabricated trades over SyntheticDaily symbols → real build_events; deterministic, CI smoke) and `RealCongressBundle` (loader + PolygonDaily + insider `enrich_events`; min_filed = window start − 5 days) + `run_congress_strategy(strategy, bundle, risk_cfg)` mirroring the insider twin (Strategy gains tolerated `congress: dict` config, `kind="congress"` dispatch, roles-style validation → ValueError → 400).
- **Lab wiring** — `_provider_factory` branch for `session == "congress"`; CANONICAL += 3 variants; seed backfill picks them up automatically; gauntlet excluded (kind != "single").

### Canonical variants

| Name | Config |
|---|---|
| Congress: member buys | min_amount 15_001 |
| Congress: power buys | power_only=True |
| Congress: cluster buys | cluster_n=2, cluster_window_days=30 |

### UI

Generalize the Lab badge: `kind === "insider"` → amber "insider" (existing); `kind === "congress"` → violet "congress". One conditional, same pill idiom.

## Testing

Mirror the insider suite: loader fixture-JSON tests (both chambers, dependent-owner and options rows dropped, idempotency), signal-rule unit tests (size floor, owner filter, days_to_file cap, power filter, cluster window, trigger strictly-after + min_filed), bundle determinism + density (CI smoke ≥1 trade at days=60), Strategy round-trip + dispatch + 400-on-bad-config, Lab API create/run, seed backfill includes congress names.

## Non-goals (v1)

Sell/short side; options rows; committee-sector matching (rejected by evidence); Senate-side paid APIs; live polling.
