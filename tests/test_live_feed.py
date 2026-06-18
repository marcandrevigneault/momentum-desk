"""The live aggregator turns a Snapshot stream into the same closed MinuteBars
the backtester feeds the engine: OHLC of `last`, running cum_volume/vwap off the
latest snapshot, ET minute-of-day, and per-bar volume as the cum delta."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from momentum_desk.live_feed import MinuteBarAggregator, tod_of
from momentum_desk.models import Snapshot

_ET = ZoneInfo("America/New_York")


def _ts(h: int, m: int, s: int = 0) -> float:
    """Epoch seconds for 2026-06-16 HH:MM:SS ET."""
    return datetime(2026, 6, 16, h, m, s, tzinfo=_ET).timestamp()


def _snap(sym: str, last: float, cum: int, vwap: float, ts: float) -> Snapshot:
    return Snapshot(symbol=sym, last=last, prev_close=10.0, day_open=10.0, vwap=vwap,
                    cum_volume=cum, avg_volume_20d=1e6, ts=ts)


def test_tod_of_is_et_minute_of_day():
    assert tod_of(_ts(9, 30)) == 570
    assert tod_of(_ts(4, 0)) == 240
    assert tod_of(_ts(11, 0)) == 660


def test_polygon_data_ts_reveals_real_print_time():
    """The feed-freshness fix: derive the true market-print time so the UI can
    measure delay instead of asserting it."""
    from momentum_desk.adapters.polygon import _data_ts
    assert abs(_data_ts({"lastTrade": {"t": 1781740796648281544}}) - 1781740796.648) < 1
    assert _data_ts({"min": {"t": 1781740740000}}) == 1781740740.0   # ms fallback
    assert _data_ts({}) == 0.0                                       # unknown


def test_no_bar_until_minute_closes():
    agg = MinuteBarAggregator()
    assert agg.ingest(_snap("AAA", 5.0, 100, 5.0, _ts(9, 30, 1))) is None
    assert agg.ingest(_snap("AAA", 5.1, 200, 5.05, _ts(9, 30, 30))) is None


def test_bar_emitted_on_rollover_with_ohlc_and_running_totals():
    agg = MinuteBarAggregator()
    agg.ingest(_snap("AAA", 5.0, 100, 5.00, _ts(9, 30, 1)))   # open
    agg.ingest(_snap("AAA", 5.4, 180, 5.10, _ts(9, 30, 20)))  # high
    agg.ingest(_snap("AAA", 4.8, 240, 5.05, _ts(9, 30, 40)))  # low
    agg.ingest(_snap("AAA", 5.2, 300, 5.07, _ts(9, 30, 58)))  # close
    bar = agg.ingest(_snap("AAA", 5.25, 320, 5.08, _ts(9, 31, 2)))  # next minute → emit 09:30
    assert bar is not None
    assert (bar.o, bar.h, bar.l, bar.c) == (5.0, 5.4, 4.8, 5.2)
    assert bar.tod == 570
    assert bar.t == 0
    assert bar.cum_volume == 300        # last snapshot's running total in that minute
    assert bar.vwap == 5.07
    assert bar.v == 300                 # first bar: full session volume so far


def test_per_bar_volume_is_cum_delta():
    agg = MinuteBarAggregator()
    agg.ingest(_snap("AAA", 5.0, 300, 5.0, _ts(9, 30, 1)))
    agg.ingest(_snap("AAA", 5.2, 500, 5.1, _ts(9, 31, 1)))   # closes 09:30 (cum 300)
    bar2 = agg.ingest(_snap("AAA", 5.3, 650, 5.2, _ts(9, 32, 1)))  # closes 09:31 (cum 500)
    assert bar2.tod == 571
    assert bar2.t == 1
    assert bar2.cum_volume == 500
    assert bar2.v == 200                # 500 - 300


def test_flush_releases_the_open_minute():
    agg = MinuteBarAggregator()
    agg.ingest(_snap("AAA", 5.0, 100, 5.0, _ts(15, 59, 10)))
    agg.ingest(_snap("AAA", 5.3, 160, 5.1, _ts(15, 59, 50)))
    bar = agg.flush("AAA")
    assert bar is not None
    assert bar.tod == 959 and bar.c == 5.3 and bar.cum_volume == 160
    assert agg.flush("AAA") is None     # nothing left


def test_symbols_are_independent():
    agg = MinuteBarAggregator()
    agg.ingest(_snap("AAA", 5.0, 100, 5.0, _ts(9, 30, 1)))
    agg.ingest(_snap("BBB", 20.0, 400, 20.0, _ts(9, 30, 1)))
    a = agg.ingest(_snap("AAA", 5.1, 150, 5.05, _ts(9, 31, 1)))
    b = agg.ingest(_snap("BBB", 21.0, 900, 20.5, _ts(9, 31, 1)))
    assert a.cum_volume == 100 and a.o == 5.0
    assert b.cum_volume == 400 and b.o == 20.0


def test_emitted_bars_drive_the_engine():
    """The aggregator's output feeds SymbolTracker.on_bar without adaptation —
    proving the live path produces exactly the bar shape the engine expects."""
    from momentum_desk.backtest.data import DayCandidate
    from momentum_desk.edge.portfolio import _policy
    from momentum_desk.edge.screen import ScreenConfig
    from momentum_desk.live_engine import SymbolTracker

    cand = DayCandidate(symbol="AAA", day="2026-06-16", prev_close=4.0, day_open=5.0,
                        avg_volume_20d=1e6)
    tracker = SymbolTracker(cand, ScreenConfig(session="intraday"), _policy("pct_trail_10"))
    agg = MinuteBarAggregator()
    # a rising tape: each minute closes higher than the last
    for i in range(20):
        h, m = 9, 30 + i
        agg.ingest(_snap("AAA", 5.0 + i * 0.1, 1000 * (i + 1), 5.0 + i * 0.05, _ts(h, m, 5)))
        bar = agg.ingest(_snap("AAA", 5.0 + i * 0.1, 1000 * (i + 1), 5.0 + i * 0.05, _ts(h, m + 1, 1)))
        if bar is not None:
            tracker.on_bar(bar)   # never raises; consumes the live bar shape
    assert tracker.state in ("watch", "holding", "done")
