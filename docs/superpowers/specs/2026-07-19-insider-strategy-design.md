# Insider-Filing Strategy — Design

**Date:** 2026-07-19
**Status:** Approved for implementation (Phase 1)
**Scope:** New event-driven strategy type based on SEC Form 4 insider transactions, backtestable in the Lab. Live/paper trading wiring is explicitly Phase 2.

## Motivation

Add a "news-based insider trading" strategy family: enter positions when corporate insiders (CEO/CFO/officers/directors) file significant open-market purchases, conditioned on company market cap, sector, and recent news. The empirical literature (Cohen/Malloy/Pomorski 2012; Lakonishok & Lee 2001; Jeng/Metrick/Zeckhauser 2003) supports purchase-side alpha decaying over weeks-to-months, amplified for cluster buys, executive (vs. director) purchases, and small caps.

Originally framed around "QualitativeQuant" email alerts — that service does not exist (likely confusion with Quiver Quantitative). No vendor is needed: all signal data comes free from the SEC.

## Data sources (decided)

| Need | Source | Notes |
|---|---|---|
| Backtest history | SEC quarterly **Insider Transactions Data Sets** (`{YYYY}q{N}_form345.zip`, 2006Q1+) | Structured TSVs: SUBMISSION, REPORTINGOWNER, NONDERIV_TRANS. Resolve URLs from the index page (path prefix changed mid-2026). |
| Live signals (Phase 2) | EDGAR `getcurrent` Atom feed, type=4 | ~1 min latency; poll 1–5 min; parse `ownershipDocument` XML. |
| Daily prices (multi-day holds) | Polygon daily aggregates via existing `CachedClient` pattern | `SyntheticDailyHistory` fallback for CI/tests. |
| Market cap / sector | Polygon `/v3/reference/tickers` (primary, key already present), Finnhub `/stock/profile2` fallback | Cached per symbol in SQLite. |
| Recent news | Polygon `/v2/reference/news` (pattern already in `PolygonHistory._premarket_news`) | Boolean + headline, windowed before filing date (no lookahead). |

SEC fair-access rules: max 10 req/s, mandatory `User-Agent: momentum-desk marcandre.vigneault.96@gmail.com`.

**Point-in-time discipline:** the tradable event is the EDGAR **filing acceptance datetime**, never the transaction date (insiders file up to 2 business days late). Entry is next session open strictly after acceptance.

## Signal construction (from the literature, all config-toggleable)

1. **Purchases only, transaction code `P`** (open-market buy). Excludes A/M/F/G — grants, exercises, tax, gifts.
2. **Min transaction value** (default $25K).
3. **Role weighting/filter:** CEO/CFO > other officers > directors; 10%-owners-only filings excluded by default.
4. **Cluster detection:** ≥N distinct insiders buying within a W-trading-day window (default 2 within 10).
5. **Routine filter:** drop insiders who traded in the same calendar month in each of the prior 3 years (Cohen-Malloy-Pomorski heuristic); drop `aff10b5One` filings.
6. **Conditioning dimensions** for variant testing: market-cap bucket (small <$2B / mid / large), sector, has-recent-news flag.

## Architecture

The existing pipeline is bar-driven intraday breakout (session finders in `edge/screen.py`, single-day portfolio loop in `edge/portfolio.py` that force-closes at EOD). An insider strategy is event-triggered and holds multi-day. Rather than contort the intraday machinery, add a **parallel event-strategy engine** that plugs in at the `Strategy`/`AccountRun` seam so the Lab store, leaderboard, metrics, and UI all work unchanged.

New package `momentum_desk/insider/`:

- **`edgar.py`** — download/parse quarterly form345 ZIPs into `data/insider.db` (SQLite): table `filings(accession PK, symbol, filed_at, transaction_date, code, shares, price, value, is_ceo, is_cfo, is_officer, is_director, is_ten_pct, officer_title, tenb5_1, shares_owned_after, owner_name)`. Idempotent per quarter; cached ZIPs under `data/cache/edgar/`.
- **`signals.py`** — pure functions: `build_events(filings, cfg) -> list[InsiderEvent]` applying rules 1–5. `InsiderEvent`: symbol, trigger date (first tradable day), aggregate value, n_insiders, top_role, conviction (value / holdings-after), flags.
- **`enrich.py`** — cached per-symbol profile lookup (market cap, sector) + news flag near the filing date. Missing data ⇒ `None`, never an exception; filters treat `None` per config (`include_unknown`).
- **`prices.py`** — `DailyBar` + `DailyProvider` protocol; `PolygonDaily` (cached) and `SyntheticDaily` (deterministic, seeds fake events for CI smoke).
- **`simulate.py`** — daily-bar portfolio simulator: entry at next open after trigger; exits = time stop (N trading days), hard stop %, trailing stop % — first hit wins, gap-through fills at open; `max_concurrent` / `max_gross_pct` caps; equal-risk or equal-notional sizing; commissions + slippage consistent with `edge/portfolio.py`. Returns the shared `AccountRun` via `compute_metrics`.

### Strategy integration

- `Strategy.kind = "insider"` with a new tolerated `insider: dict` config block (filters + holding/exit params). `to_dict`/`from_dict` untouched semantics (unknown-key tolerance already exists).
- `run_strategy` (`edge/strategy.py`) dispatches `kind == "insider"` → `insider.simulate.run_insider`.
- `edge/lab.py`: `_provider_factory` learns the daily-provider path; `CANONICAL` gains seeded variants; `gauntlet_key` excludes insider strategies for now (like combos).

### Seeded variants (the "test a few different positions" ask)

| Name | Filter |
|---|---|
| Insider: all officer buys | code P, value ≥$25K, any officer/director |
| Insider: CEO/CFO buys | CEO or CFO only, ≥$25K |
| Insider: cluster buys | ≥2 insiders / 10 trading days |
| Insider: small-cap cluster | cluster + market cap <$2B |
| Insider: news-quiet buys | officer buys with no news in prior 5 days (Fidrmuc et al.: news-preceded trades less informative) |

Each seeded at hold=20 trading days, trail 15%, hard stop 20%; the optimizer sweep can vary these later.

### Lab UI

Minimal: insider runs appear in the existing leaderboard (they already will, via `AccountRun`). Add a small "insider" badge in the strategy row when `kind == "insider"`. No new editor UI in Phase 1.

## Error handling

- SEC download failures: retry w/ backoff; a missing quarter logs and skips (backtest proceeds on available quarters).
- No Polygon key: falls back to `SyntheticDaily` + synthetic events (same pattern as `_provider_factory` today).
- Symbols missing from price data (delistings, OTC): event skipped, counted in `skips`.
- Enrichment gaps: `None`-tolerant filters as above.

## Testing

- Parser: small fixture TSVs (hand-built, ~10 rows) → exact `filings` rows.
- Signals: unit tests per rule (code-P filter, cluster window edges, routine-month heuristic, min value).
- Simulator: deterministic synthetic scenario with known OHLC → exact trade log (entry at next open, stop/trail/time precedence, gap fills).
- Strategy round-trip + dispatch (`test_strategy.py` pattern).
- Lab API: create/run an insider strategy via `POST /api/lab/strategies` + `/api/lab/run` with synthetic providers.
- CI smoke: extend with a synthetic insider run asserting `trades > 0`.

## Phase 2 (out of scope here)

Live EDGAR Atom polling ingress, `LiveEngine` support for event entries, ARMED/paper transmission of multi-day positions, Gmail-alert ingestion (if user re-authorizes Gmail and subscribes to an alert service).

## Non-goals

- Short/sale-side signals (no alpha per literature).
- Derivative-table (options) transactions in Phase 1.
- Pre-2006 history; institutional datasets; paid vendors.
