"""Daily expenses (never deleted – voided with a reason)."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import or_, select

from ..db.base import q2, session_scope
from ..db.models import Expense
from . import settings
from .audit import audit
from .auth import current_user, require
from .business_day import assert_day_open, current_business_date
from .errors import BusinessError, DuplicateError, NotFoundError, ValidationError
from .sales import PAYMENT_MODES


def categories() -> list[str]:
    return list(settings.get("expense_categories") or [])


def _validate(s, d: date, category: str, amount, mode: str) -> tuple[date, Decimal]:
    cur = current_business_date(s)
    d = d or cur
    if d > cur:
        raise ValidationError("Expense date cannot be in the future.")
    assert_day_open(s, d, "add or change expenses on that date")
    if not (category or "").strip():
        raise ValidationError("Choose an expense category.")
    amt = q2(amount)
    if amt <= 0:
        raise ValidationError("Enter the expense amount.")
    if mode not in PAYMENT_MODES:
        raise ValidationError("Choose the payment mode.")
    return d, amt


def add_expense(category: str, amount, *, description: str = "", payment_mode: str = "CASH",
                notes: str = "", expense_date: date | None = None, client_ref: str | None = None) -> int:
    require("expenses")
    with session_scope() as s:
        if client_ref:
            dup = s.scalar(select(Expense.id).where(Expense.client_ref == client_ref))
            if dup is not None:
                raise DuplicateError("This expense was already saved.")
        d, amt = _validate(s, expense_date, category, amount, payment_mode)
        e = Expense(business_date=d, category=category.strip(), description=(description or "").strip(), amount=amt,
                    payment_mode=payment_mode, notes=(notes or "").strip(), created_by=current_user().id,
                    client_ref=client_ref)
        s.add(e)
        s.flush()
        audit(s, "EXPENSE_ADDED", "expense", e.id, {"category": e.category, "amount": amt, "date": d})
        return e.id


def update_expense(expense_id: int, *, category: str, amount, description: str = "", payment_mode: str = "CASH",
                   notes: str = "", expense_date: date | None = None) -> None:
    require("expenses")
    with session_scope() as s:
        e = s.get(Expense, expense_id)
        if e is None:
            raise NotFoundError("Expense not found.")
        if e.status != "ACTIVE":
            raise BusinessError("This expense is voided.")
        assert_day_open(s, e.business_date, "edit this expense")
        d, amt = _validate(s, expense_date or e.business_date, category, amount, payment_mode)
        before = dict(category=e.category, amount=e.amount, date=e.business_date, mode=e.payment_mode)
        e.business_date, e.category, e.amount, e.payment_mode = d, category.strip(), amt, payment_mode
        e.description, e.notes = (description or "").strip(), (notes or "").strip()
        audit(s, "EXPENSE_UPDATED", "expense", e.id, {"before": before,
                                                     "after": dict(category=e.category, amount=amt, date=d,
                                                                   mode=payment_mode)})


def void_expense(expense_id: int, reason: str) -> None:
    require("expenses")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Please enter a reason.")
    with session_scope() as s:
        e = s.get(Expense, expense_id)
        if e is None:
            raise NotFoundError("Expense not found.")
        if e.status != "ACTIVE":
            raise BusinessError("This expense is already voided.")
        assert_day_open(s, e.business_date, "void this expense")
        e.status, e.voided_at, e.void_reason = "VOID", datetime.now().replace(microsecond=0), reason
        audit(s, "EXPENSE_VOIDED", "expense", e.id, {"amount": e.amount, "category": e.category, "reason": reason})


def list_expenses(start: date | None = None, end: date | None = None, category: str | None = None,
                  search: str = "", include_void: bool = False) -> list[dict]:
    with session_scope() as s:
        q = select(Expense).order_by(Expense.business_date.desc(), Expense.id.desc())
        if not include_void:
            q = q.where(Expense.status == "ACTIVE")
        if start:
            q = q.where(Expense.business_date >= start)
        if end:
            q = q.where(Expense.business_date <= end)
        if category:
            q = q.where(Expense.category == category)
        if search:
            like = f"%{search}%"
            q = q.where(or_(Expense.description.ilike(like), Expense.notes.ilike(like), Expense.category.ilike(like)))
        return [dict(id=e.id, business_date=e.business_date, category=e.category, description=e.description,
                     amount=e.amount, payment_mode=e.payment_mode, notes=e.notes, status=e.status,
                     created_at=e.created_at, void_reason=e.void_reason) for e in s.scalars(q)]


def totals_by_category(start: date, end: date) -> dict[str, Decimal]:
    out: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    for e in list_expenses(start, end):
        out[e["category"]] += e["amount"]
    return dict(out)
