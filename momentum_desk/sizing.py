"""Position-sizing modes for the live loop.

  * ``fixed``     — risk a constant % of a fixed book size (the first-test mode;
                    does NOT scale with the account).
  * ``nav-kelly`` — risk a FRACTION of the growth-optimal Kelly bet, applied to
                    the LIVE account NAV — so size scales with the account.

Kelly. For trades measured in R (units of the per-trade risk), risking a fraction
``f`` of capital returns ``f·R`` on the bankroll; the growth-optimal ``f`` (first
order) is ``f* = E[R] / E[R²]``. Full Kelly is far too aggressive on a fat-tailed
edge (a single bad cluster ruins you), so we trade a *fraction* of it — quarter
Kelly by default. The default ``f*`` is measured from the validated 5-year
R-distribution (mean R ≈ 0.80, E[R²] ≈ 10.2 → f* ≈ 0.079, i.e. full Kelly would
risk ~7.9% of NAV per trade; quarter-Kelly ≈ 2%).
"""
from __future__ import annotations

from dataclasses import dataclass

# E[R]/E[R²] over the 5-year sim (9,824 trades). Full Kelly = risk this fraction
# of NAV per trade; we apply only a fraction of it.
STRATEGY_KELLY_FSTAR = 0.079


def kelly_fstar(rs: list[float]) -> float:
    """Growth-optimal risk fraction f* = E[R]/E[R²] from a list of R-multiples,
    clamped to [0, 1]. (First-order Kelly: maximises E[log(1+f·R)].)"""
    if not rs:
        return 0.0
    n = len(rs)
    m = sum(rs) / n
    m2 = sum(r * r for r in rs) / n
    if m2 <= 0:
        return 0.0
    return max(0.0, min(1.0, m / m2))


@dataclass
class SizingConfig:
    mode: str = "fixed"            # "fixed" | "nav-kelly" | "conviction"
    kelly_fraction: float = 0.25   # fraction of full Kelly (¼-Kelly default — tail-safe)
    fstar: float = STRATEGY_KELLY_FSTAR
    max_risk_pct: float = 2.5      # hard cap on risk-%/trade, whatever the mode says
    # --- conviction mode: scale risk up on the strongest signals ---
    base_risk_pct: float = 1.0     # risk on an ordinary signal
    conviction_max_pct: float = 8.0  # risk on a top-conviction signal (the "evident" trades)
    score_lo: float = 8.0          # scanner score mapped to base_risk_pct
    score_hi: float = 20.0         # scanner score mapped to conviction_max_pct

    def risk_pct(self) -> float:
        """The risk-per-trade % for nav-kelly (capped)."""
        return min(self.max_risk_pct, self.kelly_fraction * self.fstar * 100.0)

    def conviction_risk_pct(self, score: float) -> float:
        """Conviction-scaled risk: ordinary signals risk base_risk_pct, the
        strongest ('more evident') scale up toward conviction_max_pct, hard-capped
        at max_risk_pct. `score` is the scanner's signal score."""
        c = (score - self.score_lo) / (self.score_hi - self.score_lo) if self.score_hi > self.score_lo else 0.0
        c = max(0.0, min(1.0, c))
        pct = self.base_risk_pct + (self.conviction_max_pct - self.base_risk_pct) * c
        return min(self.max_risk_pct, max(0.0, pct))
