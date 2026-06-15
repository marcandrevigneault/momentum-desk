"""The feature library: every variable a strategy can see, made explicit.

Each feature is a named, documented function of a `FeatureContext` — the exact,
point-in-time information knowable at the moment of an entry decision (the
candidate's slow-moving context plus the intraday bars up to and including the
entry bar). Features are tagged `static` (fixed once at/before the open) or
`dynamic` (moves with price/volume as the session unfolds) so the platform can
show you, for any strategy, which knobs are constants and which adapt to state.

A feature returns `None` when it can't be computed (e.g. float unknown); callers
drop those rows for that feature rather than guessing.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..backtest.data import MARKET_OPEN_TOD, DayCandidate, MinuteBar


@dataclass
class FeatureContext:
    """Everything knowable at the instant of an entry decision — no lookahead.

    `bars` are the session bars *up to and including* the entry bar; `entry_idx`
    indexes the entry bar within them; `entry_price` is the modelled fill.
    """

    cand: DayCandidate
    bars: list[MinuteBar]
    entry_idx: int
    entry_price: float
    session: str

    @property
    def eb(self) -> MinuteBar:
        return self.bars[self.entry_idx]

    def recent(self, n: int) -> list[MinuteBar]:
        """The last `n` bars up to and including the entry bar."""
        lo = max(0, self.entry_idx - n + 1)
        return self.bars[lo : self.entry_idx + 1]


Kind = str  # "static" | "dynamic"


@dataclass
class Feature:
    name: str
    kind: Kind
    desc: str
    fn: Callable[[FeatureContext], float | None]


# ---- helpers (stdlib only) -------------------------------------------------

def _true_range(prev_close: float, b: MinuteBar) -> float:
    return max(b.h - b.l, abs(b.h - prev_close), abs(b.l - prev_close))


def _atr(bars: list[MinuteBar]) -> float | None:
    if len(bars) < 2:
        return None
    trs = [_true_range(bars[i - 1].c, bars[i]) for i in range(1, len(bars))]
    return sum(trs) / len(trs) if trs else None


# ---- feature functions -----------------------------------------------------
# Each takes a FeatureContext and returns a float (or None when undefined).

def _gap_pct(c: FeatureContext) -> float:
    return c.cand.gap_pct


def _float_m(c: FeatureContext) -> float | None:
    return None if c.cand.float_shares is None else c.cand.float_shares / 1e6


def _has_news(c: FeatureContext) -> float:
    return 1.0 if c.cand.has_news else 0.0


def _price(c: FeatureContext) -> float:
    return c.entry_price


def _rvol(c: FeatureContext) -> float | None:
    return c.eb.cum_volume / c.cand.avg_volume_20d if c.cand.avg_volume_20d > 0 else None


def _ext_vwap_pct(c: FeatureContext) -> float | None:
    v = c.eb.vwap
    return 100.0 * (c.entry_price - v) / v if v > 0 else None


def _move_from_open_pct(c: FeatureContext) -> float | None:
    # Point-in-time anchor. In the pre-market session the 09:30 `day_open` hasn't
    # happened yet, so referencing it leaks the future — use the first known
    # (pre-market) bar's open instead. Regular/intraday entries are after 09:30,
    # where day_open is already known.
    o = c.bars[0].o if c.session == "premarket" else c.cand.day_open
    return 100.0 * (c.entry_price - o) / o if o > 0 else None


def _minutes_since_open(c: FeatureContext) -> float:
    # negative in the pre-market session (entry before 09:30)
    return float(c.eb.tod - MARKET_OPEN_TOD)


def _tod(c: FeatureContext) -> float:
    return float(c.eb.tod)


def _atr_pct(c: FeatureContext) -> float | None:
    a = _atr(c.recent(14))
    return 100.0 * a / c.entry_price if a is not None and c.entry_price > 0 else None


def _vol_accel(c: FeatureContext) -> float | None:
    prior = c.recent(6)[:-1]  # the 5 bars before the entry bar
    if not prior:
        return None
    avg = sum(b.v for b in prior) / len(prior)
    return c.eb.v / avg if avg > 0 else None


def _consec_green(c: FeatureContext) -> float:
    n = 0
    for b in reversed(c.bars[: c.entry_idx + 1]):
        if b.c > b.o:
            n += 1
        else:
            break
    return float(n)


def _range_expansion(c: FeatureContext) -> float | None:
    prior = c.recent(11)[:-1]  # 10 bars before entry
    if not prior:
        return None
    avg_rng = sum(b.h - b.l for b in prior) / len(prior)
    return (c.eb.h - c.eb.l) / avg_rng if avg_rng > 0 else None


def _dist_from_hod_pct(c: FeatureContext) -> float | None:
    hod = max(b.h for b in c.bars[: c.entry_idx + 1])
    return 100.0 * (hod - c.entry_price) / hod if hod > 0 else None


def _pullback_depth_pct(c: FeatureContext) -> float | None:
    """How deep the worst dip was, as a % of the run from low to high so far —
    a proxy for how 'clean' the move into entry was."""
    seg = c.bars[: c.entry_idx + 1]
    lo = min(b.l for b in seg)
    hi = max(b.h for b in seg)
    return 100.0 * (hi - c.entry_price) / (hi - lo) if hi > lo else None


FEATURES: list[Feature] = [
    Feature("gap_pct", "static", "Open vs prior close (%).", _gap_pct),
    Feature("float_m", "static", "Float / shares-outstanding (millions); None if unknown.", _float_m),
    Feature("has_news", "static", "1 if a catalyst was published pre-open, else 0.", _has_news),
    Feature("price", "dynamic", "Entry price ($).", _price),
    Feature("rvol", "dynamic", "Session volume so far / trailing-20d avg.", _rvol),
    Feature("ext_vwap_pct", "dynamic", "Extension of entry above session VWAP (%).", _ext_vwap_pct),
    Feature("move_from_open_pct", "dynamic", "Entry vs the day's open (%).", _move_from_open_pct),
    Feature("minutes_since_open", "dynamic", "Minutes after 09:30 at entry (<0 = pre-market).", _minutes_since_open),
    Feature("tod", "dynamic", "Entry time-of-day (ET minutes from midnight).", _tod),
    Feature("atr_pct", "dynamic", "14-bar ATR as a % of price (volatility).", _atr_pct),
    Feature("vol_accel", "dynamic", "Entry-bar volume / avg of prior 5 bars.", _vol_accel),
    Feature("consec_green", "dynamic", "Consecutive up-bars into the entry.", _consec_green),
    Feature("range_expansion", "dynamic", "Entry-bar range / avg of prior 10 bars.", _range_expansion),
    Feature("dist_from_hod_pct", "dynamic", "Distance of entry below high-of-day (%).", _dist_from_hod_pct),
    Feature("pullback_depth_pct", "dynamic", "Entry's position in the low→high run so far (%).", _pullback_depth_pct),
]

FEATURES_BY_NAME = {f.name: f for f in FEATURES}
