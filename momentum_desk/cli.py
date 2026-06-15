"""Runnable demo: stream the mock feed through the scanner and risk engine and
print a live leaderboard. Zero dependencies, zero credentials, runs any time.

    python -m momentum_desk.cli            # ~12 ticks of the mock morning
    python -m momentum_desk.cli --ticks 40

This is the console proof that the pipeline works end to end. The real-time
web dashboard and the IBKR (paper-first) execution layer build on these exact
pieces.
"""
from __future__ import annotations

import argparse
import time

from .adapters import MockReplayAdapter
from .models import Flag
from .risk import RiskEngine
from .scanner import ScannerEngine

_FLAG_LABEL = {
    Flag.EXTENDED: "EXTENDED-don't chase",
    Flag.THIN_BOOK: "THIN-you'd be liquidity",
    Flag.HALTED: "HALTED",
    Flag.NO_CATALYST: "no-catalyst",
    Flag.UNKNOWN_FLOAT: "float?",
}


def main() -> None:
    ap = argparse.ArgumentParser(description="Momentum Desk — mock scanner demo")
    ap.add_argument("--ticks", type=int, default=12, help="number of scan ticks")
    ap.add_argument("--interval", type=float, default=0.4, help="seconds between ticks")
    args = ap.parse_args()

    feed = MockReplayAdapter()
    scanner = ScannerEngine()
    risk = RiskEngine()

    print(f"Momentum Desk · feed={feed.name} · universe={', '.join(feed.universe())}")
    print("(mock data — fabricated prices, for development only)\n")

    for i in range(args.ticks):
        signals = scanner.scan(feed.poll())
        print(f"── tick {i + 1:>2}/{args.ticks} " + "─" * 48)
        if not signals:
            print("   no candidates in band")
        for s in signals:
            tag = "✓" if s.actionable else "·"
            flags = " ".join(_FLAG_LABEL[f] for f in s.flags)
            line = (f"   {tag} {s.symbol:<5} ${s.last:<6.2f} gap {s.gap_pct:>5.1f}%  "
                    f"rvol {s.relative_volume:>4.1f}x  +VWAP {s.extension_above_vwap_pct:>5.1f}%  "
                    f"score {s.score:>5.1f}")
            if flags:
                line += f"   [{flags}]"
            print(line)
            # demonstrate risk sizing on the single best actionable name
            if s.actionable and s is signals[0]:
                stop = round(s.last * 0.95, 2)  # illustrative 5% stop
                plan = risk.plan(_snap_for(feed, s.symbol), entry=s.last, stop=stop)
                if plan.ok:
                    note = f" ({'; '.join(plan.reasons)})" if plan.reasons else ""
                    print(f"       → risk-sized: {plan.shares} sh @ ${plan.entry:.2f}, "
                          f"stop ${plan.stop:.2f}, risking ${plan.risk_dollars:.0f}{note}")
                else:
                    print(f"       → REJECTED: {'; '.join(plan.reasons)}")
        time.sleep(args.interval)

    print("\nDone. This is mock data — wire a real feed + IBKR paper account next.")


def _snap_for(feed: MockReplayAdapter, symbol: str):
    for snap in feed.poll():
        if snap.symbol == symbol:
            return snap
    raise KeyError(symbol)


if __name__ == "__main__":
    main()
