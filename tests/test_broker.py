"""SimBroker fills + P&L — the sim desk's execution math is money-adjacent
(the dashboard's paper practice P&L comes from here), so it stays covered.
The real order path is covered in test_live_transmit.py / test_ibkr_cp.py."""
from __future__ import annotations

from momentum_desk.broker import Order, OrderSide, OrderType, SimBroker


def test_sim_fill_and_realized_pnl():
    b = SimBroker()
    b.place_order(Order("ABCD", OrderSide.BUY, 100, OrderType.LMT, limit_price=5.0))
    assert b.positions()[0].quantity == 100
    close = b.place_order(Order("ABCD", OrderSide.SELL, 100, OrderType.LMT, limit_price=5.5))
    assert close.realized_pnl == 50.0                    # (5.5-5.0)*100
    assert b.positions() == []                           # flat again


def test_sim_rejects_unpriced_market_order():
    b = SimBroker()
    res = b.place_order(Order("ABCD", OrderSide.BUY, 100, OrderType.MKT))  # no ref price
    assert res.status == "rejected"
