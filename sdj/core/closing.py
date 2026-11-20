"""User-controlled daily closing with cash reconciliation, locking and audited reopening."""
from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select

from .. import config
from ..db.base import q2, session_scope
from ..db.models import DailyClosing
from ..output.report_model import Report
from . import settings
from .audit import audit
from .auth import current_user, require
from .business_day import current_business_date, is_day_closed, unclosed_days_before
from .errors import BusinessError, ValidationError
from .reports import build_day_report

log = logging.getLogger(__name__)


def suggested_opening_cash(d: date) -> Decimal:
    with session_scope() as s:
        prev = s.scalar(select(DailyClosing).where(DailyClosing.business_date < d)
                        .order_by(DailyClosing.business_date.desc()))
        if prev is not None:
            return prev.carry_forward
    return q2(settings.get("default_opening_cash") or 0)


def closing_status(d: date | None = None) -> dict:
    with session_scope() as s:
        cur = current_business_date(s)
        d = d or cur
        rec = s.scalar(select(DailyClosing).where(DailyClosing.business_date == d))
        pending = unclosed_days_before(s, d)
        return dict(date=d, current_business_date=cur, closed=bool(rec and rec.is_locked),
                    reopened=bool(rec and not rec.is_locked), pending_before=pending,
                    closed_by=rec.closed_by_name if rec else "", closed_at=rec.closed_at if rec else None)


def preview(d: date, opening_cash=None, actual_cash=None, carry_forward=None) -> Report:
    if opening_cash is None:
        opening_cash = suggested_opening_cash(d)
    return build_day_report(d, opening_cash, actual_cash or 0, carry_forward=carry_forward)


def close_day(d: date, actual_cash, opening_cash=None, notes: str = "", make_pdf: bool = True,
              make_backup: bool = True, carry_forward=None) -> dict:
    require("daily_closing")
    actual = q2(actual_cash)
    if actual < 0:
        raise ValidationError("Actual cash cannot be negative.")
    carry = actual if carry_forward is None else q2(carry_forward)
    if carry < 0 or carry > actual:
        raise ValidationError("Cash kept in the drawer must be between zero and the cash counted.")
    with session_scope() as s:
        cur = current_business_date(s)
        if d > cur:
            raise BusinessError("You cannot close a future date.")
        if is_day_closed(s, d):
            raise BusinessError(f"{d.strftime('%d %b %Y')} is already closed.")
        pending = unclosed_days_before(s, d)
        if pending:
            raise BusinessError("Please close earlier days first: " + ", ".join(x.strftime('%d %b %Y') for x in pending))
    opening = q2(opening_cash) if opening_cash is not None else suggested_opening_cash(d)
    rep = build_day_report(d, opening, actual, status="CLOSED", carry_forward=carry)
    f_total = rep.kv("Summary", "Total revenue")
    fin = rep.section("Financial report")
    vals = {k.label: k.value for k in fin.kvs}
    pdf_path = config.reports_dir() / f"Daily_Closing_{d.isoformat()}.pdf"
    user = current_user()
    with session_scope() as s:
        rec = s.scalar(select(DailyClosing).where(DailyClosing.business_date == d))
        payload = dict(opening_cash=opening, cash_received=rep.kv("Cash reconciliation", "Cash received"),
                       cash_expenses=rep.kv("Cash reconciliation", "Cash expenses"),
                       cash_refunds=rep.kv("Cash reconciliation", "Cash refunds"),
                       cash_supplier_payments=rep.kv("Cash reconciliation", "Cash paid to suppliers"),
                       expected_cash=rep.kv("Cash reconciliation", "Expected closing cash"), actual_cash=actual,
                       carry_forward=carry,
                       difference=rep.kv("Cash reconciliation", "Cash difference"), total_revenue=f_total,
                       net_sales=vals["Net sales (goods)"], gross_profit=vals["Gross profit"],
                       net_profit=vals["Net operating profit"], summary_json=rep.to_json(), pdf_path=str(pdf_path),
                       notes=(notes or "").strip(), closed_by=user.id, closed_by_name=user.display_name,
                       closed_at=datetime.now().replace(microsecond=0), status="CLOSED", is_locked=True)
        if rec is None:
            rec = DailyClosing(business_date=d, close_count=1, **payload)
            s.add(rec)
        else:  # re-closing a reopened day
            for k, v in payload.items():
                setattr(rec, k, v)
            rec.close_count = (rec.close_count or 1) + 1
        s.flush()
        audit(s, "DAY_CLOSED", "daily_closing", rec.id, {"date": d, "expected_cash": payload["expected_cash"],
                                                        "actual_cash": actual, "difference": payload["difference"],
                                                        "carry_forward": carry,
                                                        "net_profit": payload["net_profit"]})
        closing_id = rec.id
    result = dict(id=closing_id, pdf_path=None, backup=None, pdf_error=None, report=rep)
    if make_pdf:
        try:
            from ..output.pdf_report import render_report_pdf
            render_report_pdf(rep, pdf_path)
            result["pdf_path"] = str(pdf_path)
        except Exception as exc:  # closing is saved even if the PDF fails; it can be re-exported
            log.exception("closing PDF failed")
            result["pdf_error"] = str(exc)
    if make_backup:
        try:
            from .backup import backup_now
            result["backup"] = str(backup_now("closing"))
        except Exception:
            log.exception("backup after closing failed")
        from .offsite import after_closing          # encrypted copy for the phones and the 2nd backup folder
        result["offsite"] = after_closing(d, result.get("pdf_path"))
    return result


def reopen_day(d: date, reason: str) -> None:
    require("reopen_day")
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise ValidationError("Please describe why the day is being reopened (at least 5 characters).")
    with session_scope() as s:
        rec = s.scalar(select(DailyClosing).where(DailyClosing.business_date == d))
        if rec is None or not rec.is_locked:
            raise BusinessError("This day is not closed.")
        later = s.scalar(select(DailyClosing.id).where(DailyClosing.business_date > d, DailyClosing.is_locked.is_(True)))
        if later:
            raise BusinessError("Reopen later closed days first (most recent first), so the cash chain stays correct.")
        user = current_user()
        old_snapshot = rec.summary_json
        rec.is_locked = False
        rec.status = "REOPENED"
        rec.reopened_by_name, rec.reopened_at, rec.reopen_reason = user.display_name, datetime.now().replace(microsecond=0), reason
        audit(s, "DAY_REOPENED", "daily_closing", rec.id, {"date": d, "reason": reason,
                                                          "previous_actual_cash": rec.actual_cash,
                                                          "previous_net_profit": rec.net_profit,
                                                          "previous_snapshot": old_snapshot[:20000]})


def list_closings(limit: int = 400) -> list[dict]:
    with session_scope() as s:
        return [dict(id=c.id, business_date=c.business_date, status=c.status, is_locked=c.is_locked,
                     total_revenue=c.total_revenue, net_sales=c.net_sales, gross_profit=c.gross_profit,
                     net_profit=c.net_profit, expected_cash=c.expected_cash, actual_cash=c.actual_cash,
                     difference=c.difference, carry_forward=c.carry_forward, closed_by=c.closed_by_name, closed_at=c.closed_at, pdf_path=c.pdf_path,
                     close_count=c.close_count, reopen_reason=c.reopen_reason)
                for c in s.scalars(select(DailyClosing).order_by(DailyClosing.business_date.desc()).limit(limit))]


def saved_report(d: date) -> Report | None:
    with session_scope() as s:
        rec = s.scalar(select(DailyClosing).where(DailyClosing.business_date == d))
        if rec is None or not rec.summary_json:
            return None
        return Report.from_json(rec.summary_json)
