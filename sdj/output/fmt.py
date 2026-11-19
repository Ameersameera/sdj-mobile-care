"""Display formatting – Indian number grouping (1,23,456.00) and dates."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from ..db.base import D, q2

RUPEE = "₹"


def _group_indian(int_part: str) -> str:
    if len(int_part) <= 3:
        return int_part
    head, tail = int_part[:-3], int_part[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups) + "," + tail


def num(value, decimals: int = 2) -> str:
    v = q2(value) if decimals == 2 else D(value).quantize(Decimal(1) if decimals == 0 else Decimal("0." + "0" * decimals))
    neg = v < 0
    s = f"{abs(v):.{decimals}f}"
    if decimals:
        ip, fp = s.split(".")
        out = _group_indian(ip) + "." + fp
    else:
        out = _group_indian(s)
    return ("-" if neg else "") + out


def inr(value, symbol: bool = True, decimals: int = 2) -> str:
    if value is None or value == "":
        return ""
    v = D(value)
    s = num(abs(v), decimals)
    s = f"{RUPEE}{s}" if symbol else s
    return f"-{s}" if v < 0 else s


def qty(value) -> str:
    if value is None or value == "":
        return ""
    return num(int(value), 0)


def dt(value) -> str:
    if not value:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d %b %Y %I:%M %p")
    if isinstance(value, date):
        return value.strftime("%d %b %Y")
    return str(value)


def d_only(value) -> str:
    if not value:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d %b %Y")
    if isinstance(value, date):
        return value.strftime("%d %b %Y")
    return str(value)


def tm(value) -> str:
    if isinstance(value, datetime):
        return value.strftime("%I:%M %p")
    return ""


def pct(value) -> str:
    return f"{D(value):.1f}%"


def format_value(value, kind: str) -> str:
    if value is None:
        return ""
    if kind == "money":
        return inr(value)
    if kind == "money0":
        return inr(value, decimals=0)
    if kind == "int":
        return qty(value)
    if kind == "date":
        return d_only(value)
    if kind == "datetime":
        return dt(value)
    if kind == "pct":
        return pct(value)
    return str(value)
