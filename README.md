# Momentum Desk

A low-float momentum **research platform and trading desk**, built
**paper-first**, connected to Interactive Brokers. Strategies are validated in
the Strategy Lab (feature screens, exit-policy lab, an anti-self-deception
gauntlet, full account simulation) and the exact same entry/exit code is then
replayed on the live tape by the reconciled engine — so what you validated is
what you trade.

```bash
# 1) console demo — no install, no credentials (mock data)
python -m momentum_desk.cli

# 2) dashboard (two terminals)
python -m venv .venv && . .venv/bin/activate
pip install -e . && pip install fastapi "uvicorn[standard]" pyyaml
uvicorn momentum_desk.server:app --port 8000      # backend (mock feed by default)

cd web && npm install && npm run dev               # dashboard → http://localhost:5180
```

To use real data: copy `config.example.yaml` to `config.yaml`, set
`data_feed: polygon` and your `polygon_api_key`, and restart the backend.
A misconfigured feed now **fails loudly** — the desk refuses to start rather
than silently serving synthetic data.

---

## Read this before you trust it with a dollar

This project started from the idea of "copy-trading Ross Cameron." Three facts
shaped what it actually is:

1. **You cannot reverse-engineer his trades from earnings reports.** Verified
   earnings pages are aggregate P&L and statement screenshots — not a
   timestamped, ticker-level trade log — and they're after the fact. There is
   no live feed of anyone's trades; by the time a trade is public you're late.
   So this tool encodes the *setup criteria*, not "his trades."

2. **The FTC measured the outcome of following this strategy.** In 2022 the FTC
   charged Warrior Trading / Ross Cameron with deceptive earnings claims; they
   settled for **$3M** and the FTC found *"the vast majority of customers
   actually lost money trading."* ~$2.9M was returned to harmed customers.
   ([press release](https://www.ftc.gov/news-events/news/press-releases/2022/04/federal-trade-commission-cracks-down-warrior-trading-misleading-consumers-false-investment-promises) ·
   [refunds](https://www.ftc.gov/news-events/news/press-releases/2023/01/ftc-returns-more-29-million-consumers-harmed-warrior-trading))

3. **A faster way to take losing trades loses money faster.** If you "usually
   lose and become exit liquidity," the fix isn't a better entry alert — it's
   mechanical risk control, validation on historical data, and a guard that
   tells you when your own order is too big for the tape. Those are the parts
   of this repo that matter most.

**This is not financial advice and carries no promise of profit. Trade on paper
until a rule set is proven, and never risk money you can't lose.**

---

## How it works

```
                     ┌── RESEARCH (offline, Strategy Lab) ─────────────────────┐
                     │ feature screen → exit lab → rules/optimize → gauntlet   │
                     │ → account sim (portfolio/combo) → SQLite leaderboard    │
                     └───────────────────────────┬─────────────────────────────┘
                                                 │ ★ active Strategy
data feed → Snapshot ─┬─> scanner (flags+score) ─┼─> Cockpit (display + paper practice)
                      │                          │
                      └─> minute bars ─> reconciled engine (same edge code, live)
                                          → RiskEngine sizing → decide() guards
                                          → IBKR paper: MKT entry + resting STP stop
```

Two pipelines share the feed and the risk engine, with different jobs:

- **Cockpit (display + practice).** `scanner.py` filters Snapshots into the
  low-float / RVOL / gap / news band with **anti-chase flags** (`EXTENDED`,
  `HALTED`, `UNKNOWN_FLOAT`); `paper.py` is a simulated practice desk with a
  trailing stop. Nothing here ever reaches a broker — it's the market view.
- **The reconciled engine (the trading path).** `live_tracker.SymbolTracker`
  replays the *exact* backtest entry (`edge/screen._find_event`) and exit
  (`edge/exits.simulate_exit_detail`) bar-by-bar on the live tape, sized by the
  same `RiskEngine`. Backtest and live agree by construction (`test_live_tracker`).
- **Transmission (`live_transmit.py`).** Armed entries go out as a market
  parent **plus a broker-resident protective stop child** in one submission —
  if the desk dies mid-hold, the stop is already resting at IBKR. Exits cancel
  the stop and close only while the broker still holds the symbol. Arming
  requires `LIVE_ENGINE_ENABLED` + `IBKR_ENABLED` + `LIVE_TRADING=armed`, and a
  paper (DU*) account is re-asserted on every transmit.
- **The journal (`journal.py`).** Every engine intent, transmit decision, and
  order outcome is appended to `journal/live-<day>.jsonl` — dry-run days
  included. Review a session with `python -m momentum_desk.journal <file>`.

## The Strategy Lab (research)

Everything under `momentum_desk/edge/` exists to answer one question honestly:
*where is the edge, and does it survive scrutiny?* (Design doc:
`docs/EDGE_PLATFORM.md`.)

- **`screen.py`** — per-feature information coefficients + decile lift, with
  the discretionary filters *recorded as features rather than applied*.
- **`exits.py`** — the same entries through 9 exit policies, compared in R.
- **`optimize.py`** — grid search over entry filters × exits, plus the cached
  instant evaluator behind the dashboard's Tuner tab. Every search winner is
  **deflated** (`stats.deflate_best`) against the number of trials.
- **`gauntlet.py`** — bootstrap CIs, deflated Sharpe, purged walk-forward with
  selection, regime breakdown, untouched holdout → SURVIVES / FRAGILE / REJECTED.
- **`portfolio.py` / `combo.py`** — full account simulation (sizing, capacity,
  slippage, daily-loss breaker) for single and multi-leg strategies.
- **`store.py` / `lab.py`** — strategies + runs in SQLite (`data/lab.db`),
  ranked on the dashboard leaderboard; the ★ active strategy is what the live
  engine runs. Re-run any strategy from the leaderboard's re-run button or
  `POST /api/lab/run`.

Honesty is enforced in the fills: no lookahead, adverse slippage on entries and
exits, and when a bar touches both stop and target the **stop fills first**.
Synthetic data proves only that the machinery computes; real conclusions come
from Polygon history (cached under `data/cache/polygon/`). Known methodological
limits are tracked in `docs/EDGE_PLATFORM.md` and affect magnitude, not
direction.

## Status

- [x] Scanner + anti-chase flags, mechanical risk engine, WebSocket dashboard
- [x] Edge platform: screen, exit lab, optimizer/tuner/rules, gauntlet, account sim
- [x] Strategy Lab: SQLite store, leaderboard, activate/rename/re-run
- [x] Reconciled live engine on the real tape (dry-run by default)
- [x] IBKR Client Portal connection: NAV/positions read + **armable paper
      transmission with broker-resident protective stops** (flag-gated)
- [x] Trade journal wired into the armed path (signals, decisions, fills)
- [ ] Fill reconciliation: mark journal fills against actual IBKR executions
- [ ] Halt/LULD modeling in the backtest fills

## Safety defaults

`mode: paper` and `data_feed: mock` ship as defaults; credentials live only in
`config.yaml` (gitignored) or env secrets. Real transmission needs three
explicit flags, re-asserts a paper (DU) account on every drain, sends **no
entry without a stop**, and trips a daily-loss breaker that halts new entries
while still allowing exits. Going live remains a deliberate, explicit switch.
