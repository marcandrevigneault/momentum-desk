"""The first-class Strategy object — the keystone of the Strategy Lab.

Today a "strategy" is scattered across config objects: ScreenConfig (session/
entry), an exit-policy string, RiskConfig (sizing), SimConfig (concurrency), and
ComboLeg lists for multi-leg books. The Lab needs ONE thing it can store in a
database, rank on a leaderboard, edit in the UI, and run. That's ``Strategy``.

``run_strategy`` dispatches a Strategy to the right engine — the single-strategy
account simulator or the multi-leg combo — and both return the shared
``AccountRun`` shape (see edge/result.py), so the caller never branches on kind.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from ..risk import RiskConfig
from .combo import ComboConfig, ComboLeg, run_combo
from .portfolio import SimConfig, run_simulation
from .result import AccountRun


@dataclass
class SizingSpec:
    """How a strategy sizes each trade. ``fixed`` risks a constant % of the
    starting book; ``compound`` risks the same % of the *current* book (the
    "% equity" mode); ``conviction`` scales risk by signal strength (deferred —
    stored but only the backtest harness uses it yet)."""
    mode: str = "fixed"            # fixed | compound | conviction
    risk_pct: float = 1.0          # % of equity risked per trade


@dataclass
class LegSpec:
    """One leg of a combo strategy (or the single leg of a plain strategy)."""
    name: str
    session: str = "intraday"      # premarket | intraday | regular
    exit_policy: str = "pct_trail_10"
    style: str = "momentum"        # momentum (long breakout) | fade (short blow-off)
    max_ext_pct: float | None = None   # optional entry filters from the edge findings
    rvol_max: float | None = None
    slippage_pct: float = 0.3


@dataclass
class Strategy:
    """A complete, runnable, storable strategy definition."""
    name: str
    kind: str = "single"           # single | combo
    session: str = "intraday"      # single-strategy session (ignored for combos)
    exit_policy: str = "pct_trail_10"
    slippage_pct: float = 0.3
    sizing: SizingSpec = field(default_factory=SizingSpec)
    max_concurrent: int = 5
    max_gross_pct: float = 100.0
    legs: list[LegSpec] = field(default_factory=list)   # combo only

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Strategy:
        d = dict(d)
        sizing = d.pop("sizing", None)
        legs = d.pop("legs", None) or []
        known = {f for f in cls.__dataclass_fields__ if f not in ("sizing", "legs")}  # type: ignore[attr-defined]
        strat = cls(**{k: v for k, v in d.items() if k in known})
        if isinstance(sizing, dict):
            strat.sizing = SizingSpec(**{k: v for k, v in sizing.items()
                                         if k in SizingSpec.__dataclass_fields__})
        strat.legs = [LegSpec(**{k: v for k, v in leg.items() if k in LegSpec.__dataclass_fields__})
                      for leg in legs if isinstance(leg, dict)]
        return strat


def _risk(strategy: Strategy, account_equity: float) -> RiskConfig:
    return RiskConfig(
        account_equity=account_equity,
        max_risk_per_trade_pct=strategy.sizing.risk_pct,
        compound=(strategy.sizing.mode == "compound"),
    )


def run_strategy(strategy: Strategy, provider_factory, *, account_equity: float = 25_000.0) -> AccountRun:
    """Run a Strategy and return the unified AccountRun. ``provider_factory`` maps
    a session name to a HistoricalProvider, so the same Strategy can run on
    synthetic or real data without the Strategy knowing about feeds."""
    risk = _risk(strategy, account_equity)
    if strategy.kind == "combo":
        legs = [
            ComboLeg(name=leg.name, provider=provider_factory(leg.session), session=leg.session,
                     exit_policy=leg.exit_policy, style=leg.style, max_ext_pct=leg.max_ext_pct,
                     rvol_max=leg.rvol_max, slippage_pct=leg.slippage_pct)
            for leg in strategy.legs
        ]
        ccfg = ComboConfig(max_concurrent=strategy.max_concurrent, max_gross_pct=strategy.max_gross_pct)
        return run_combo(legs, ccfg, risk)
    scfg = SimConfig(session=strategy.session, exit_policy=strategy.exit_policy,
                     slippage_pct=strategy.slippage_pct, max_concurrent=strategy.max_concurrent,
                     max_gross_pct=strategy.max_gross_pct)
    return run_simulation(provider_factory(strategy.session), scfg, risk)
