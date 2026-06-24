"""Position sizing: Kelly math + nav-kelly scales with the live account."""
from __future__ import annotations

from momentum_desk.broker.sim import SimBroker
from momentum_desk.live import LiveConfig, LivePaperTrader
from momentum_desk.models import Snapshot
from momentum_desk.risk import RiskConfig, RiskEngine
from momentum_desk.scanner import ScanConfig, ScannerEngine
from momentum_desk.sizing import STRATEGY_KELLY_FSTAR, SizingConfig, kelly_fstar


def test_kelly_fstar_matches_formula():
    rs = [-1.0, 2.0, -1.0, 3.0, -1.0]   # mean 0.4, E[R²] = (1+4+1+9+1)/5 = 3.2
    assert abs(kelly_fstar(rs) - 0.4 / 3.2) < 1e-9
    assert kelly_fstar([]) == 0.0
    assert kelly_fstar([0.0, 0.0]) == 0.0   # no edge → don't bet


def test_quarter_kelly_is_conservative():
    s = SizingConfig(mode="nav-kelly", kelly_fraction=0.25, fstar=STRATEGY_KELLY_FSTAR)
    # quarter of ~7.9% ≈ 2% of NAV, under the cap
    assert 1.5 < s.risk_pct() < 2.5


def test_risk_pct_is_capped():
    s = SizingConfig(mode="nav-kelly", kelly_fraction=1.0, fstar=0.20, max_risk_pct=2.5)
    assert s.risk_pct() == 2.5   # full Kelly on a big edge is clamped


class _Adapter:
    name = "stub"

    def __init__(self, snaps):
        self._snaps = snaps

    def poll(self):
        return list(self._snaps)


def _snap(sym, last=5.0):
    return Snapshot(symbol=sym, last=last, prev_close=last / 1.5, day_open=last * 0.97,
                    vwap=last * 0.95, cum_volume=50_000_000, avg_volume_20d=300_000,
                    float_shares=8e6, has_news=True)


def _trader(sizing, starting_equity):
    scanner = ScannerEngine(ScanConfig(min_relative_volume=2.0, min_gap_pct=5.0, require_news=True))
    risk = RiskEngine(RiskConfig(account_equity=starting_equity))
    broker = SimBroker(starting_equity=starting_equity)
    trader = LivePaperTrader(_Adapter([_snap("AAA")]), scanner, risk, broker,
                             LiveConfig(max_concurrent=5), sizing)
    return trader, risk, broker


def test_fixed_mode_does_not_retune_risk():
    trader, risk, _ = _trader(SizingConfig(mode="fixed"), 25_000)
    trader.step(now_tod=600)
    assert risk.config.account_equity == 25_000   # untouched


def test_conviction_scales_with_score():
    s = SizingConfig(mode="conviction", base_risk_pct=1.0, conviction_max_pct=8.0,
                     score_lo=8.0, score_hi=20.0, max_risk_pct=10.0)
    assert abs(s.conviction_risk_pct(8.0) - 1.0) < 1e-9      # weak → base
    assert abs(s.conviction_risk_pct(20.0) - 8.0) < 1e-9     # strong → conviction_max
    assert abs(s.conviction_risk_pct(14.0) - 4.5) < 1e-9     # mid → halfway
    assert s.conviction_risk_pct(2.0) == 1.0                 # below floor → base
    assert s.conviction_risk_pct(100.0) == 8.0               # above ceiling → conviction_max
    s2 = SizingConfig(mode="conviction", conviction_max_pct=20.0, max_risk_pct=10.0, score_hi=20.0)
    assert s2.conviction_risk_pct(20.0) == 10.0              # hard cap bites


def test_conviction_sizes_bigger_on_stronger_signal():
    sizing = SizingConfig(mode="conviction", base_risk_pct=1.0, conviction_max_pct=8.0, max_risk_pct=10.0)
    scanner = ScannerEngine(ScanConfig(min_relative_volume=2.0, min_gap_pct=5.0, require_news=True))
    risk = RiskEngine(RiskConfig(account_equity=25_000))
    broker = SimBroker(starting_equity=25_000)
    weak = Snapshot(symbol="WEAK", last=5.0, prev_close=4.5, day_open=4.85, vwap=4.8,
                    cum_volume=20_000_000, avg_volume_20d=2_000_000, float_shares=8e6, has_news=True)
    strong = Snapshot(symbol="STRONG", last=5.0, prev_close=2.5, day_open=4.8, vwap=4.7,
                      cum_volume=80_000_000, avg_volume_20d=300_000, float_shares=8e6, has_news=True)
    trader = LivePaperTrader(_Adapter([weak, strong]), scanner, risk, broker,
                             LiveConfig(max_concurrent=5), sizing)
    out = trader.step(now_tod=600)
    by = {a["symbol"]: a["risk_pct"] for a in out["acted"]}
    assert by.get("STRONG", 0) > by.get("WEAK", 0)   # stronger signal risked more


def test_nav_kelly_sizes_off_live_nav():
    sizing = SizingConfig(mode="nav-kelly", kelly_fraction=0.25)
    trader, risk, broker = _trader(sizing, 25_000)
    # simulate the account having grown to $100k
    broker.realized_pnl = 75_000.0
    trader.step(now_tod=600)
    assert risk.config.account_equity == 100_000          # retuned to live NAV
    assert abs(risk.config.max_risk_per_trade_pct - sizing.risk_pct()) < 1e-9
