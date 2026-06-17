"""Run the autonomous paper-trading loop. SAFE BY DEFAULT.

  * default broker is the in-memory SIM (no gateway, no orders anywhere);
  * `--broker cp` routes to the IBKR **Client Portal gateway** (the deployed
    ibeam path) on a PAPER account, but still DRY-RUN (logs the orders,
    transmits nothing) unless you ALSO pass `--send`;
  * `--broker tws` uses the legacy ib_async/TWS socket adapter;
  * a `HALT` file (./data/HALT or $HALT_FILE) flattens + stops — the kill switch;
  * Ctrl-C also flattens everything and disconnects.

    # dry rehearsal against IBKR paper through the gateway (nothing transmits):
    DATA_FEED=polygon POLYGON_API_KEY=... python -m scripts.live_paper --broker cp
    # actually send paper orders (real-time data strongly recommended):
    DATA_FEED=polygon POLYGON_API_KEY=... python -m scripts.live_paper --broker cp --send
"""
from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from momentum_desk.broker import IBKRBroker, IBKRCPBroker, SimBroker
from momentum_desk.config import build_adapter, load_config
from momentum_desk.live import LiveConfig, LivePaperTrader
from momentum_desk.risk import RiskConfig, RiskEngine
from momentum_desk.scanner import ScannerEngine
from momentum_desk.sizing import SizingConfig


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--broker", choices=["sim", "cp", "tws"], default="sim",
                    help="sim | cp (Client Portal gateway / ibeam — the deployed path) | tws (ib_async)")
    ap.add_argument("--send", action="store_true", help="actually transmit (paper); default is dry-run")
    ap.add_argument("--gw-url", default=os.environ.get("IBKR_GATEWAY_URL", "https://localhost:5000/v1/api"))
    ap.add_argument("--port", type=int, default=7497, help="TWS paper port (--broker tws)")
    ap.add_argument("--equity", type=float, default=25_000.0, help="fallback book size if NAV is unavailable")
    ap.add_argument("--risk-pct", type=float, default=1.0, help="risk per trade (% of live NAV)")
    ap.add_argument("--max-concurrent", type=int, default=10)
    ap.add_argument("--max-trades", type=int, default=20)
    ap.add_argument("--trail-pct", type=float, default=10.0)
    ap.add_argument("--poll", type=int, default=30)
    # sizing: fixed (1% of live NAV — compounds) | nav-kelly | conviction (task #6)
    ap.add_argument("--sizing", choices=["fixed", "nav-kelly", "conviction"], default="fixed")
    ap.add_argument("--kelly-fraction", type=float, default=0.25)
    ap.add_argument("--max-risk-pct", type=float, default=None, help="hard cap (default 2.5; 10 for conviction)")
    ap.add_argument("--halt-file", default=os.environ.get("HALT_FILE", "data/HALT"))
    args = ap.parse_args()

    cap = args.max_risk_pct if args.max_risk_pct is not None else (10.0 if args.sizing == "conviction" else 2.5)
    sizing = SizingConfig(mode=args.sizing, kelly_fraction=args.kelly_fraction, max_risk_pct=cap)

    cfg = load_config()
    adapter = build_adapter(cfg)
    scanner = ScannerEngine(cfg.scanner)
    risk = RiskEngine(RiskConfig(account_equity=args.equity, max_risk_per_trade_pct=args.risk_pct))

    if args.broker == "cp":
        broker = IBKRCPBroker(gateway_url=args.gw_url, account_id=os.environ.get("IBKR_ACCOUNT_ID", ""),
                              dry_run=not args.send)
        broker.connect()   # raises clearly if the gateway isn't authenticated / not paper
    elif args.broker == "tws":
        broker = IBKRBroker(port=args.port, dry_run=not args.send)   # paper port enforced inside
        broker.connect()
    else:
        broker = SimBroker(starting_equity=args.equity)
        broker.connect()

    lcfg = LiveConfig(trail_pct=args.trail_pct, max_concurrent=args.max_concurrent,
                      max_trades_day=args.max_trades, poll_interval_s=args.poll)
    trader = LivePaperTrader(adapter, scanner, risk, broker, lcfg, sizing)
    halt = Path(args.halt_file)

    live = args.broker != "sim" and args.send
    print("=" * 64)
    print(f"  feed={adapter.name}  broker={broker.name}  "
          f"{'LIVE-PAPER (transmitting)' if live else 'DRY-RUN (no orders sent)'}")
    print(f"  caps: {lcfg.max_concurrent} concurrent · {lcfg.max_trades_day} trades/day · "
          f"{lcfg.trail_pct}% trail · window {lcfg.session_start_tod // 60}:{lcfg.session_start_tod % 60:02d}"
          f"–{lcfg.session_end_tod // 60}:{lcfg.session_end_tod % 60:02d} ET · flatten {lcfg.flatten_tod // 60}:00")
    print(f"  sizing: {args.sizing} · {args.risk_pct}% per trade (of live NAV when reported) · cap {cap}%")
    print(f"  kill switch: create {halt} to flatten + stop, or Ctrl-C")
    print("=" * 64)

    try:
        while True:
            if halt.exists():
                print(f"  HALT file {halt} present — flattening + stopping")
                print("  flattened:", trader.flatten())
                break
            out = trader.step()
            for a in out.get("acted", []):
                print(f"  [{a['tod']}] ENTER {a['symbol']} x{a['shares']} @ ~{a['entry']} "
                      f"trail {a['trail_pct']}% → {a['results']}")
            if out.get("flattened"):
                print(f"  [{out['tod']}] FLATTEN → {out['flattened']}")
            time.sleep(args.poll)
    except KeyboardInterrupt:
        print("\n  kill switch — flattening + disconnecting…")
        print("  flattened:", trader.flatten())
    finally:
        broker.disconnect()


if __name__ == "__main__":
    main()
