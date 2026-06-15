"""Calibrate our scanner's detection against Ross Cameron's ACTUAL trades.

For each (date, ticker) he traded, reconstruct what our scanner would have seen
that day (price, gap vs prior close, and the ticker's rank among that day's
in-band gappers) and report recall: of his names, how many our universe surfaces
— at our default top-N cap, and looser. Precision is intentionally NOT measured:
he's one human who can't trade every mover, so flagging more than he traded is
expected and fine.

    POLYGON_API_KEY=... python scripts/calibrate.py
"""
from __future__ import annotations

import datetime as dt
import os

from momentum_desk.backtest.http import CachedClient

KEY = os.environ["POLYGON_API_KEY"]
client = CachedClient("https://api.polygon.io", KEY, cache_dir="data/cache/polygon", max_per_min=0)

# our scanner gate (defaults, after Ross-trade calibration)
MIN_PRICE, MAX_PRICE, MIN_GAP = 1.0, 30.0, 10.0
TOP_N = 20   # PolygonHistory default max_candidates_per_day

TRADES = [
    ("2026-06-15", "CUPR"), ("2026-06-15", "JRSH"), ("2026-06-11", "EDHL"),
    ("2026-06-11", "QH"), ("2026-06-11", "FGL"), ("2026-06-10", "DSY"),
    ("2026-06-10", "VSME"), ("2026-06-10", "CLWT"), ("2026-06-10", "GCDT"),
    ("2026-06-10", "KIDZ"), ("2026-06-10", "QH"), ("2026-06-09", "AZI"),
    ("2026-06-09", "AHMA"), ("2026-06-09", "PAVS"), ("2026-06-09", "DAIC"),
    ("2026-06-09", "ELPW"), ("2026-06-09", "UK"), ("2026-06-08", "INHD"),
    ("2026-06-04", "STI"), ("2026-06-02", "BJDX"), ("2026-06-01", "MASK"),
    ("2026-06-01", "MTEK"), ("2026-06-01", "HKIT"), ("2026-05-29", "OLOX"),
    ("2026-05-29", "CMND"),
]


def grouped(day: str) -> dict:
    r = client.get_json(f"/v2/aggs/grouped/locale/us/market/stocks/{day}", {"adjusted": "true"})
    return {b["T"]: b for b in (r.get("results") or [])}


def prior_trading_day(day: str):
    d = dt.date.fromisoformat(day)
    for _ in range(6):
        d -= dt.timedelta(days=1)
        g = grouped(d.isoformat())
        if g:
            return g
    return {}


def gappers_ranked(today: dict, prev: dict) -> list[tuple[float, str]]:
    out = []
    for sym, b in today.items():
        p = prev.get(sym)
        o, c = b.get("o", 0), (p or {}).get("c", 0)
        if c <= 0 or not (MIN_PRICE <= o <= MAX_PRICE):
            continue
        g = 100.0 * (o - c) / c
        if g >= MIN_GAP:
            out.append((g, sym))
    out.sort(reverse=True)
    return out


print(f"\nRoss-universe calibration · gate: ${MIN_PRICE}-${MAX_PRICE}, gap>={MIN_GAP}%, top-{TOP_N}/day")
print("─" * 86)
print(f"  {'date':<12}{'ticker':<8}{'price':>8}{'gap%':>8}{'rank':>7}{'/gappers':>10}  verdict")
hit_top, hit_loose, in_band_gate, total_eval = 0, 0, 0, 0
misses = []
for date, tk in TRADES:
    today = grouped(date)
    if not today:
        print(f"  {date:<12}{tk:<8}{'—':>8}{'—':>8}{'—':>7}{'—':>10}  no grouped data (today/holiday?)")
        continue
    prev = prior_trading_day(date)
    bar, pbar = today.get(tk), prev.get(tk)
    if not bar or not pbar:
        print(f"  {date:<12}{tk:<8}{'—':>8}{'—':>8}{'—':>7}{'—':>10}  ticker not in tape")
        misses.append((date, tk, "not in tape"))
        continue
    total_eval += 1
    price, prevc = bar.get("o", 0), pbar.get("c", 0)
    gap = 100.0 * (price - prevc) / prevc if prevc else 0.0
    ranked = gappers_ranked(today, prev)
    rank = next((i + 1 for i, (_g, s) in enumerate(ranked) if s == tk), None)
    passes_gate = (MIN_PRICE <= price <= MAX_PRICE) and gap >= MIN_GAP
    if passes_gate:
        in_band_gate += 1
    if rank and rank <= TOP_N:
        hit_top += 1
    if passes_gate:
        hit_loose += 1
    if not (rank and rank <= TOP_N):
        why = (f"price ${price:.2f} out of band" if not (MIN_PRICE <= price <= MAX_PRICE)
               else f"gap {gap:.0f}% < {MIN_GAP}%" if gap < MIN_GAP
               else f"rank {rank} > top-{TOP_N}")
        misses.append((date, tk, why))
    verdict = ("✓ top-N" if (rank and rank <= TOP_N)
               else "· passes gate, outside top-N" if passes_gate else "✗ filtered")
    print(f"  {date:<12}{tk:<8}{price:>8.2f}{gap:>8.0f}{(rank or '—'):>7}{len(ranked):>10}  {verdict}")

print("─" * 86)
print(f"  evaluated {total_eval} of {len(TRADES)} (rest: missing tape/today)")
print(f"  RECALL  top-{TOP_N}: {hit_top}/{total_eval}   passes-gate (any rank): {in_band_gate}/{total_eval}")
if misses:
    print("\n  misses & why:")
    for d, t, w in misses:
        print(f"    {d} {t}: {w}")
