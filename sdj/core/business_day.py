"""Business-day logic.

* A transaction belongs to the *current business date*: today's calendar date,
  or – if today has already been closed by the owner – the next unclosed date.
* A closed day is locked: its transactions cannot be cancelled or edited until an
  admin reopens it (audited).
* Stock is never reset; quantities simply carry forward.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select, union

from ..db.models import DailyClosing, Expense, Payment, Sale, ServiceJob, StockMovement
from . import syncctx
from .errors import DayClosedError


def today() -> date:
    return date.today()


def closed_dates(session) -> set[date]:
    return set(session.scalars(select(DailyClosing.business_date).where(DailyClosing.is_locked.is_(True))))


def is_day_closed(session, d: date) -> bool:
    return session.scalar(select(DailyClosing.id).where(DailyClosing.business_date == d,
                                                        DailyClosing.is_locked.is_(True))) is not None


def current_business_date(session) -> date:
    entry = syncctx.current()
    if entry is not None and entry.business_date is not None:
        return entry.business_date          # an entry from a phone keeps the day it was made on
    d = today()
    closed = closed_dates(session)
    while d in closed:
        d += timedelta(days=1)
    return d


def assert_day_open(session, d: date, what: str = "change this record") -> None:
    if is_day_closed(session, d):
        entry = syncctx.current()
        if entry is not None and entry.allow_closed_day:
            entry.closed_days.add(d)        # saved anyway; the owner is asked to update that closing
            return
        raise DayClosedError(
            f"The business day {d.strftime('%d %b %Y')} is closed and locked, so you cannot {what}. "
            "An admin can reopen the day from Daily Closing if a correction is really needed.")


def activity_dates(session) -> set[date]:
    q = union(
        select(Sale.business_date),
        select(Payment.business_date),
        select(Expense.business_date).where(Expense.status == "ACTIVE"),
        select(StockMovement.business_date),
        select(ServiceJob.received_date),
        select(ServiceJob.completed_date).where(ServiceJob.completed_date.is_not(None)),
    )
    return {r[0] for r in session.execute(q) if r[0] is not None}


def unclosed_days_before(session, d: date) -> list[date]:
    """Days before *d* that had activity but were never closed (oldest first)."""
    closed = closed_dates(session)
    return sorted(x for x in activity_dates(session) if x < d and x not in closed)
