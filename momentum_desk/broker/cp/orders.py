"""Pure order-payload builders for the IBKR CP API.

No I/O, no side effects — just dicts the client POSTs. See
https://www.interactivebrokers.com/campus/ibkr-api-page/cpapi-v1/#submit-order
for field shapes.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Literal
from uuid import uuid4

OrderSide = Literal["BUY", "SELL"]
TIF = Literal["DAY", "GTC"]


def _num(v: Decimal | int | float) -> float:
    """IBKR wants a JSON number; Decimal -> float is fine for prices/qty."""
    return float(v)


def build_market_order(
    account_id: str,
    conid: int,
    side: OrderSide,
    quantity: Decimal | int | float,
    *,
    tif: TIF = "DAY",
    outside_rth: bool = False,
) -> dict:
    return {
        "acctId": account_id,
        "conid": int(conid),
        "orderType": "MKT",
        "side": side,
        "quantity": _num(quantity),
        "tif": tif,
        "cOID": uuid4().hex,
        "outsideRTH": outside_rth,
    }


def build_stop_order(
    account_id: str,
    conid: int,
    side: OrderSide,
    quantity: Decimal | int | float,
    stop_price: Decimal | int | float,
    *,
    tif: TIF = "GTC",
    outside_rth: bool = False,
) -> dict:
    """Standalone STP order — the protective stop that accompanies every entry."""
    return {
        "acctId": account_id,
        "conid": int(conid),
        "orderType": "STP",
        "side": side,
        "quantity": _num(quantity),
        "auxPrice": _num(stop_price),
        "tif": tif,
        "cOID": uuid4().hex,
        "outsideRTH": outside_rth,
    }


def build_limit_order(
    account_id: str,
    conid: int,
    side: OrderSide,
    quantity: Decimal | int | float,
    limit_price: Decimal | int | float,
    *,
    tif: TIF = "DAY",
    outside_rth: bool = False,
) -> dict:
    return {
        "acctId": account_id,
        "conid": int(conid),
        "orderType": "LMT",
        "side": side,
        "quantity": _num(quantity),
        "price": _num(limit_price),
        "tif": tif,
        "cOID": uuid4().hex,
        "outsideRTH": outside_rth,
    }


def build_trailing_stop_order(
    account_id: str,
    conid: int,
    side: OrderSide,
    quantity: Decimal | int | float,
    trailing_percent: Decimal | int | float,
    *,
    tif: TIF = "GTC",
    outside_rth: bool = False,
) -> dict:
    """A broker-managed trailing stop (IBKR ratchets it behind price). The desk
    pairs every entry with one of these so the protection survives a loop crash."""
    return {
        "acctId": account_id,
        "conid": int(conid),
        "orderType": "TRAIL",
        "side": side,
        "quantity": _num(quantity),
        "trailingType": "%",
        "trailingAmt": _num(trailing_percent),
        "tif": tif,
        "cOID": uuid4().hex,
        "outsideRTH": outside_rth,
    }


def build_bracket(
    *,
    account_id: str,
    parent_conid: int,
    side: OrderSide,
    quantity: Decimal | int | float,
    entry_limit: Decimal | int | float,
    stop_loss: Decimal | int | float,
    take_profit: Decimal | int | float,
    tif: TIF = "GTC",
    outside_rth: bool = False,
) -> list[dict]:
    """Entry-limit parent + stop-loss + take-profit children, the children
    referencing the parent via ``parentId`` = parent ``cOID``."""
    parent_coid = uuid4().hex
    opposite: OrderSide = "SELL" if side == "BUY" else "BUY"
    qty = _num(quantity)
    conid = int(parent_conid)

    parent = {
        "acctId": account_id, "conid": conid, "orderType": "LMT", "side": side,
        "quantity": qty, "price": _num(entry_limit), "tif": tif,
        "cOID": parent_coid, "outsideRTH": outside_rth,
    }
    stop_child = {
        "acctId": account_id, "conid": conid, "orderType": "STP", "side": opposite,
        "quantity": qty, "auxPrice": _num(stop_loss), "tif": tif,
        "cOID": uuid4().hex, "parentId": parent_coid, "outsideRTH": outside_rth,
    }
    tp_child = {
        "acctId": account_id, "conid": conid, "orderType": "LMT", "side": opposite,
        "quantity": qty, "price": _num(take_profit), "tif": tif,
        "cOID": uuid4().hex, "parentId": parent_coid, "outsideRTH": outside_rth,
    }
    return [parent, stop_child, tp_child]
