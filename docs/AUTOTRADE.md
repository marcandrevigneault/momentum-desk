# Autonomous paper trading

The autonomous loop (`momentum_desk/live.py`, run via `scripts/live_paper.py`)
polls the scanner and routes **actionable** signals to a broker under hard caps.
It is conservative by construction and **off by default** everywhere.

## Safety model
- **Sim by default / dry-run by default.** `--broker sim` touches nothing; even
  `--broker cp` (the IBKR Client Portal gateway) is dry-run unless you add `--send`.
- **Paper account enforced.** `IBKRCPBroker` refuses to route into a non-paper
  account (id not `DU…`) without `allow_live=True`.
- **Hard caps every step:** max concurrent positions (10), max trades/day (20),
  one entry per symbol/day, and a session **window** — no entries before 09:30 or
  after 11:00 ET, flatten everything at 12:00 ET.
- **Broker-managed protective exit.** Every entry is paired with a 10% trailing
  stop placed *at the broker* (`route_plan(trail_pct=...)`), so protection
  survives even if the loop dies.
- **Daily-loss breaker** halts new entries; a `HALT` file (`/app/data/HALT`) or
  Ctrl-C flattens everything and stops.
- **Container:** the `autotrade` supervisord program is `autostart=false` — a
  deploy never starts trading.

## Sizing
Fixed **1% risk per trade, off the live NAV** — i.e. 1% of the *current* account
each step (it compounds as the book grows). `nav-kelly` and `conviction` modes
exist but are deferred until backtested (see the conviction-sizing task).

## Run it
Local dry rehearsal (no orders transmit):
```bash
DATA_FEED=polygon POLYGON_API_KEY=... python -m scripts.live_paper --broker cp
```
Send paper orders:
```bash
DATA_FEED=polygon POLYGON_API_KEY=... python -m scripts.live_paper --broker cp --send
```
In the deployed container (after you trust it):
```bash
fly ssh console --app momentum-desk-mav
supervisorctl start autotrade        # stop it: supervisorctl stop autotrade
# emergency: touch /app/data/HALT     (flattens + stops)
```

## ⚠️ Live ≠ backtest
The live entry trigger is the real-time `ScannerEngine.actionable` **screen**
(gap / RVOL / extension / float / news). That is **not** the same logic as the
backtested `_simulate_intraday` opening-range/HOD-break entry that produced the
simulator's equity curves. Treat the backtest as directional, not predictive of
live results, until the two entry paths are reconciled. Also: the loop needs
`DATA_FEED=polygon` — on the default mock feed it would "trade" fake data.
