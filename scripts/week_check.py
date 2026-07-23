"""Weekly live-fire check of the congress paper strategies.

Refreshes STOCK Act disclosures, takes the events that triggered during the
last 5 trading sessions, enters them exactly as the strategies would (next
open, $25k book, 5-slot cap), and marks open positions at the latest close.
Appends a dated report to data/reports/week-check.log.

Scheduled via launchd (see docs/EDGE_PLATFORM.md) — Fridays after the close —
but safe to run by hand any time:  .venv/bin/python scripts/week_check.py
"""
import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.chdir(REPO)

import yaml  # noqa: E402

key = ""
if os.path.exists("config.yaml"):
    key = (yaml.safe_load(open("config.yaml")) or {}).get("polygon_api_key", "")
if key and not os.environ.get("POLYGON_API_KEY"):
    os.environ["POLYGON_API_KEY"] = key
if not os.environ.get("POLYGON_API_KEY"):
    sys.exit("no polygon key (config.yaml or POLYGON_API_KEY) — cannot run")

from momentum_desk.congress.loader import CongressStore  # noqa: E402
from momentum_desk.congress.signals import CongressConfig, build_events  # noqa: E402
from momentum_desk.insider.models import InsiderConfig  # noqa: E402
from momentum_desk.insider.prices import PolygonDaily  # noqa: E402
from momentum_desk.insider.simulate import run_insider  # noqa: E402
from momentum_desk.risk import RiskConfig  # noqa: E402

os.makedirs("data/reports", exist_ok=True)
LOG = os.path.join("data", "reports", "week-check.log")


def log(line: str) -> None:
    with open(LOG, "a") as fh:
        fh.write(line + "\n")
    print(line, flush=True)


log("=" * 100)
log(f"WEEKLY CONGRESS PAPER CHECK  {time.strftime('%Y-%m-%d %H:%M:%S')}")

store = CongressStore()
try:
    n = store.refresh()
    log(f"disclosure refresh: {n} new rows")
except Exception as e:  # noqa: BLE001
    log(f"refresh failed ({e!r}) — using existing store")

provider = PolygonDaily(api_key=os.environ["POLYGON_API_KEY"], days=50, max_per_min=0)
tdays = provider.trading_days()
if len(tdays) < 5:
    sys.exit("not enough trading days from provider")
week = tdays[-5:]
log(f"latest bar day: {tdays[-1]}; sessions checked: {week}")

# 60+ days of disclosure history so 30-day cluster windows have context
start = time.strftime("%Y-%m-%d", time.localtime(time.time() - 70 * 86400))
trades = store.trades(start=start)
log(f"{len(trades)} disclosure rows since {start}")

VARIANTS = {
    "cluster buys": CongressConfig(cluster_n=2, cluster_window_days=30),
    "member buys": CongressConfig(min_amount=15_001.0),
}

for name, ccfg in VARIANTS.items():
    events = [e for e in build_events(trades, ccfg, tdays, min_filed=week[0])
              if e.trigger_day >= week[0]]
    log(f"--- Congress {name}: {len(events)} events triggering this week")
    if not events:
        continue
    icfg = InsiderConfig(hold_days=ccfg.hold_days, stop_pct=ccfg.stop_pct,
                         trail_pct=ccfg.trail_pct)
    r = run_insider(events, provider, icfg, RiskConfig(),
                    max_concurrent=5, max_gross_pct=100.0)
    m = r.metrics
    log(f"    account: {m['trades']} positions, P&L ${m['total_pnl']:+,.2f} "
        f"({m['return_pct']:+.2f}% of $25k) | signals={r.n_signals} taken={r.n_taken}")
    for t in r.trades:
        status = ("OPEN (marked at last close)" if t["exit_reason"] == "end"
                  else t["exit_reason"].upper())
        log(f"    {t['symbol']:<6} in {t['day']} @ {t['entry']:<8.2f} -> "
            f"{t['exit']:<8.2f} ({t['exit_day']}) {t['shares']:>5} sh  "
            f"${t['pnl']:+9.2f}  [{status}]")

log("WEEK CHECK DONE")
