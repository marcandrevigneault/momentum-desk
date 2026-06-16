"""Instant variable evaluation off a precomputed cache.

The expensive part of a backtest is the data + per-bar exit simulation. We do
that ONCE per session and cache, for every entry event: its features
(ext_vwap_pct, rvol, move_from_open_pct) and the forward R under EACH exit
policy. Then changing any entry filter or exit becomes a pure in-memory filter +
mean — milliseconds — which is what makes the live variable editor interactive.

`build_cache` produces the cache; `evaluate` scores one variable combination.
"""
from __future__ import annotations

from .exits import POLICIES, simulate_exit
from .gauntlet import _sharpe
from .optimize import build_eval_events
from .screen import ScreenConfig

# the exit policies offered in the tuner (name → R precomputed per event)
CACHE_POLICIES = [p.name for p in POLICIES]


def build_cache(provider, cfg: ScreenConfig, slippage_pct: float = 0.3) -> list[dict]:
    """One pass over the data → a compact per-event record (features + R under
    every exit policy)."""
    events = build_eval_events(provider, cfg, slippage_pct)
    out = []
    for e in events:
        r_by = {}
        for p in POLICIES:
            r, _reason, _held = simulate_exit(e.entry, e.init_stop, e.prior, e.fwd, p, slippage_pct)
            r_by[p.name] = round(r, 4)
        out.append({"day": e.day, "ext": round(e.ext_vwap_pct, 3), "rvol": round(e.rvol, 3),
                    "move": round(e.move_from_open_pct, 3), "r": r_by})
    return out


def evaluate(cache: list[dict], *, max_ext: float | None = None, rvol_min: float = 0.0,
             rvol_max: float | None = None, min_move: float = 0.0,
             exit_policy: str = "pct_trail_10") -> dict:
    """Score one variable combination against the cache. Instant."""
    rs: list[float] = []
    by_day: dict[str, float] = {}
    for e in cache:
        if max_ext is not None and e["ext"] > max_ext:
            continue
        if e["rvol"] < rvol_min:
            continue
        if rvol_max is not None and e["rvol"] > rvol_max:
            continue
        if e["move"] < min_move:
            continue
        r = e["r"].get(exit_policy)
        if r is None:
            continue
        rs.append(r)
        by_day[e["day"]] = by_day.get(e["day"], 0.0) + r
    n = len(rs)
    if n < 5:
        return {"n": n, "expectancy_r": 0.0, "win_rate": 0.0, "profit_factor": 0.0, "daily_sharpe": 0.0}
    wins = [x for x in rs if x > 0]
    losses = [x for x in rs if x <= 0]
    gp, gl = sum(wins), -sum(losses)
    daily = [by_day[d] for d in sorted(by_day)]
    pf = round(gp / gl, 3) if gl > 0 else 999.0
    return {"n": n, "expectancy_r": round(sum(rs) / n, 4), "win_rate": round(len(wins) / n, 4),
            "profit_factor": pf, "daily_sharpe": round(_sharpe(daily), 4)}
