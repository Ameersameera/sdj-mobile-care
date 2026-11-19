"""Gap-free, unique document numbers generated inside the saving transaction."""
from __future__ import annotations

from datetime import date

from ..db.models import Counter


def financial_year_label(d: date, start_month: int = 4) -> str:
    """Indian financial year label, e.g. 19-Nov-2026 -> '26-27'."""
    start_year = d.year if d.month >= start_month else d.year - 1
    return f"{start_year % 100:02d}-{(start_year + 1) % 100:02d}"


def _next(session, name: str) -> int:
    row = session.get(Counter, name)
    if row is None:
        row = Counter(name=name, value=0)
        session.add(row)
        session.flush()
    row.value = (row.value or 0) + 1
    session.flush()
    return row.value


def next_invoice_no(session, prefix: str, d: date) -> str:
    fy = financial_year_label(d)
    n = _next(session, f"invoice_{fy}")
    return f"{prefix}/{fy}/{n:05d}"


def next_credit_note_no(session, prefix: str, d: date) -> str:
    fy = financial_year_label(d)
    n = _next(session, f"credit_note_{fy}")
    return f"{prefix}/{fy}/{n:05d}"


def next_simple(session, counter: str, prefix: str, width: int = 5) -> str:
    n = _next(session, counter)
    return f"{prefix}-{n:0{width}d}"


def next_product_code(session) -> str:
    return f"P{_next(session, 'product_code'):05d}"
