"""Safe end-to-end demo of the execution path: scan the mock feed, take the top
actionable signal, size it with the risk engine, and route entry + stop through
the SimBroker. No TWS, no real money.

    python -m momentum_desk.broker.demo

To point at a real IBKR PAPER account instead, see IBKRBroker — it defaults to
dry-run and refuses live ports without an explicit allow_live=True.
"""
from __future__ import annotations

from ..adapters import MockReplayAdapter
from ..risk import RiskEngine
from ..scanner import ScannerEngine
from .base import route_plan
from .sim import SimBroker


def main() -> None:
    feed = MockReplayAdapter()
    scanner = ScannerEngine()
    risk = RiskEngine()
    broker = SimBroker()
    broker.connect()

    snaps = list(feed.poll())
    by_symbol = {s.symbol: s for s in snaps}
    signals = scanner.scan(snaps)
    actionable = [s for s in signals if s.actionable]

    print(f"Momentum Desk · execution demo · broker={broker.name} (simulated)\n")
    if not actionable:
        print("no actionable signal this tick — nothing to route")
        return

    top = actionable[0]
    snap = by_symbol[top.symbol]
    plan = risk.plan(snap, entry=top.last, stop=round(top.last * 0.95, 2))
    print(f"top signal: {top.symbol} ${top.last:.2f}  score {top.score}")
    print(f"risk plan : {plan.shares} sh, stop ${plan.stop:.2f}, risking ${plan.risk_dollars:.0f}\n")

    results = route_plan(broker, plan, ref_price=top.last)
    for r in results:
        print(f"  {r.status:>9}  {r.symbol}  qty {r.filled_qty}  @ ${r.avg_fill_price:.2f}  {r.message}")

    # close the position to show realized P&L bookkeeping
    from .base import Order, OrderSide, OrderType
    exit_fill = broker.place_order(
        Order(top.symbol, OrderSide.SELL, plan.shares, OrderType.MKT), ref_price=round(top.last * 1.04, 2)
    )
    print(f"\n  (demo exit) sold {exit_fill.filled_qty} @ ${exit_fill.avg_fill_price:.2f} "
          f"→ realized ${exit_fill.realized_pnl:.2f}")
    print(f"  open positions now: {[(p.symbol, p.quantity) for p in broker.positions()]}")


if __name__ == "__main__":
    main()
