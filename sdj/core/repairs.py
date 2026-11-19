"""Mobile service & repair jobs.

* Spare parts are deducted from stock the moment they are added to a job
  (and returned to stock if removed / the job is cancelled).
* Income (labour + spare-parts revenue) and the parts' cost are recognised once,
  on the day the job is completed (status 'Ready for delivery' or 'Delivered').
  This is what guarantees spare-parts revenue is never counted twice.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from ..db.base import D, q2, session_scope
from ..db.models import Customer, Payment, Product, ServiceItem, ServiceJob
from . import settings
from .audit import audit
from .auth import current_user, require
from .business_day import assert_day_open, current_business_date
from .calc import allocate, split_tax
from .errors import BusinessError, DuplicateError, NotFoundError, ValidationError
from .inventory import apply_movement
from .numbering import next_simple
from .sales import PAYMENT_MODES

JOB_STATUSES = ["RECEIVED", "INSPECTION", "WAITING_PARTS", "IN_PROGRESS", "READY", "DELIVERED", "CANCELLED"]
STATUS_LABELS = {"RECEIVED": "Received", "INSPECTION": "Inspection", "WAITING_PARTS": "Waiting for spare parts",
                 "IN_PROGRESS": "Repair in progress", "READY": "Ready for delivery", "DELIVERED": "Delivered",
                 "CANCELLED": "Cancelled"}
OPEN_STATUSES = {"RECEIVED", "INSPECTION", "WAITING_PARTS", "IN_PROGRESS"}
DONE_STATUSES = {"READY", "DELIVERED"}


def clean_phone(phone: str) -> str:
    return re.sub(r"[^\d+]", "", phone or "")


def find_or_create_customer(s, name: str, phone: str, address: str = "") -> Customer:
    name, phone = (name or "").strip(), clean_phone(phone)
    if phone:
        c = s.scalar(select(Customer).where(Customer.phone == phone, Customer.is_walk_in.is_(False)).order_by(Customer.id))
        if c is not None:
            if name and c.name != name and c.name.lower().startswith("customer"):
                c.name = name
            return c
    if not name:
        raise ValidationError("Customer name is required.")
    c = Customer(name=name, phone=phone, address=(address or "").strip())
    s.add(c)
    s.flush()
    return c


def recalc_job(s, job: ServiceJob) -> None:
    gst_enabled = bool(settings.get("gst_enabled"))
    inclusive = bool(settings.get("prices_include_gst"))
    items = [i for i in job.items if i.status == "ACTIVE"]
    parts_amount = sum((i.line_amount for i in items), Decimal("0.00"))
    parts_cost = sum((i.cost_total for i in items), Decimal("0.00"))
    labour = q2(job.labour_charge)
    discount = q2(job.discount)
    if discount > labour + parts_amount:
        raise ValidationError("Discount cannot be more than labour + spare parts amount.")
    lab_share, parts_share = allocate(discount, [labour, parts_amount])
    item_shares = allocate(parts_share, [i.line_amount for i in items])
    srate = D(settings.get("service_gst_rate") or 0) if gst_enabled else Decimal("0")
    l_tax, l_gst, l_total = split_tax(labour - lab_share, srate, inclusive)
    p_tax = p_gst = p_total = Decimal("0.00")
    for it, share in zip(items, item_shares):
        rate = D(it.gst_rate) if gst_enabled else Decimal("0")
        t, g, tot = split_tax(it.line_amount - share, rate, inclusive)
        it.discount_share, it.taxable_value, it.gst_amount = share, t, g
        p_tax, p_gst, p_total = p_tax + t, p_gst + g, p_total + tot
    job.parts_amount, job.parts_cost = parts_amount, parts_cost
    job.labour_taxable, job.labour_gst = l_tax, l_gst
    job.parts_taxable, job.parts_gst = p_tax, p_gst
    job.total_amount = l_total + p_total


def _job_payment(s, job: ServiceJob, amount: Decimal, mode: str, direction: str = "IN", notes: str = "",
                 client_ref: str | None = None) -> Payment:
    if direction == "IN":
        if job.status == "DELIVERED":
            kind = "DUE_COLLECTION"
        elif job.status in DONE_STATUSES:
            kind = "SERVICE_RECEIPT"
        else:
            kind = "SERVICE_ADVANCE"
    else:
        kind = "SERVICE_REFUND"
    pay = Payment(client_ref=client_ref, business_date=current_business_date(s), direction=direction, mode=mode,
                  amount=amount, kind=kind, customer_id=job.customer_id, ref_type="SERVICE", ref_id=job.id,
                  reference=job.job_no, notes=notes, user_id=current_user().id)
    s.add(pay)
    job.paid_amount = job.paid_amount + (amount if direction == "IN" else -amount)
    return pay


def create_job(data: dict, *, advance=0, advance_mode: str = "CASH", client_ref: str | None = None) -> dict:
    require("services")
    try:
        with session_scope() as s:
            if client_ref and s.scalar(select(ServiceJob.id).where(ServiceJob.client_ref == client_ref)):
                raise DuplicateError("This job was already saved (duplicate prevented).")
            phone = clean_phone(data.get("customer_phone", ""))
            if len(re.sub(r"\D", "", phone)) < 6:
                raise ValidationError("Enter the customer's phone number (needed to inform them when ready).")
            if not (data.get("brand") or data.get("model")):
                raise ValidationError("Enter the mobile brand / model.")
            if not (data.get("problem") or "").strip():
                raise ValidationError("Describe the reported problem.")
            cust = find_or_create_customer(s, data.get("customer_name", ""), phone, data.get("address", ""))
            bdate = current_business_date(s)
            labour = q2(data.get("labour_charge") or 0)
            if labour < 0:
                raise ValidationError("Labour charge cannot be negative.")
            est = data.get("estimated_delivery")
            if est and est < bdate:
                raise ValidationError("Estimated delivery date cannot be before today.")
            wdays = int(data.get("warranty_days") if data.get("warranty_days") is not None
                        else settings.get("default_repair_warranty_days") or 0)
            job = ServiceJob(job_no=next_simple(s, "job_no", settings.get("job_prefix") or "JOB"), client_ref=client_ref,
                             customer_id=cust.id, customer_name=cust.name, customer_phone=cust.phone,
                             brand=(data.get("brand") or "").strip(), model=(data.get("model") or "").strip(),
                             imei=(data.get("imei") or "").strip(), accessories=(data.get("accessories") or "").strip(),
                             problem=(data.get("problem") or "").strip(), technician=(data.get("technician") or "").strip(),
                             received_date=bdate, estimated_delivery=est, status="RECEIVED", labour_charge=labour,
                             warranty_days=max(wdays, 0), notes=(data.get("notes") or "").strip(),
                             created_by=current_user().id)
            s.add(job)
            s.flush()
            recalc_job(s, job)
            adv = q2(advance)
            if adv < 0:
                raise ValidationError("Advance cannot be negative.")
            if adv > 0:
                if advance_mode not in PAYMENT_MODES:
                    raise ValidationError("Choose the advance payment mode.")
                _job_payment(s, job, adv, advance_mode, notes="Advance",
                             client_ref=f"{client_ref}:adv" if client_ref else None)
            s.flush()
            audit(s, "JOB_CREATED", "service_job", job.id, {"job_no": job.job_no, "customer": cust.name,
                                                           "device": f"{job.brand} {job.model}", "advance": adv})
            return dict(id=job.id, job_no=job.job_no)
    except IntegrityError as exc:
        raise DuplicateError("This job was already saved (duplicate prevented).") from exc


def _load_job(s, job_id: int) -> ServiceJob:
    job = s.get(ServiceJob, job_id)
    if job is None:
        raise NotFoundError("Service job not found.")
    return job


def _assert_editable(job: ServiceJob) -> None:
    if job.status in ("DELIVERED", "CANCELLED"):
        raise BusinessError(f"Job {job.job_no} is {STATUS_LABELS[job.status].lower()} and cannot be changed.")


def update_job(job_id: int, data: dict) -> None:
    require("services")
    with session_scope() as s:
        job = _load_job(s, job_id)
        _assert_editable(job)
        before = {k: getattr(job, k) for k in ("brand", "model", "imei", "problem", "diagnosis", "technician",
                                               "labour_charge", "discount", "warranty_days", "estimated_delivery")}
        for k in ("brand", "model", "imei", "accessories", "problem", "diagnosis", "technician", "notes"):
            if k in data:
                setattr(job, k, (data.get(k) or "").strip())
        if "estimated_delivery" in data:
            job.estimated_delivery = data["estimated_delivery"]
        money_change = any(k in data and q2(data[k] or 0) != q2(getattr(job, k)) for k in ("labour_charge", "discount"))
        if "warranty_days" in data:
            job.warranty_days = max(int(data["warranty_days"] or 0), 0)
            if job.completed_date and job.warranty_days:
                job.warranty_expiry = job.completed_date + timedelta(days=job.warranty_days)
        if money_change:
            if job.status in DONE_STATUSES:
                raise BusinessError("Charges of a completed job cannot be changed. Move it back to "
                                    "'Repair in progress' first.")
            for k in ("labour_charge", "discount"):
                if k in data:
                    v = q2(data[k] or 0)
                    if v < 0:
                        raise ValidationError("Charges cannot be negative.")
                    setattr(job, k, v)
            recalc_job(s, job)
        after = {k: getattr(job, k) for k in before}
        changes = {k: {"from": before[k], "to": after[k]} for k in before if before[k] != after[k]}
        if changes:
            audit(s, "JOB_UPDATED", "service_job", job.id, {"job_no": job.job_no, "changes": changes})


def add_part(job_id: int, product_id: int, quantity: int, unit_price=None) -> int:
    """Use a spare part on a job: stock is deducted immediately."""
    require("services")
    with session_scope() as s:
        job = _load_job(s, job_id)
        _assert_editable(job)
        if job.status in DONE_STATUSES:
            raise BusinessError("This job is marked ready. Move it back to 'Repair in progress' to add parts.")
        p = s.get(Product, product_id)
        if p is None or not p.is_active:
            raise ValidationError("Spare part not found.")
        if p.track_imei:
            raise ValidationError("IMEI-tracked phones cannot be used as spare parts.")
        qty = int(quantity)
        if qty <= 0:
            raise ValidationError("Quantity must be at least 1.")
        price = q2(p.selling_price if unit_price is None else unit_price)
        if price < 0:
            raise ValidationError("Price cannot be negative.")
        bdate = current_business_date(s)
        unit_cost = D(p.avg_cost)
        item = ServiceItem(job_id=job.id, product_id=p.id, product_name=p.name, quantity=qty, unit_price=price,
                           unit_cost=unit_cost, line_amount=q2(price * qty), cost_total=q2(unit_cost * qty),
                           gst_rate=p.gst_rate, status="ACTIVE", added_date=bdate)
        s.add(item)
        s.flush()
        apply_movement(s, p, -qty, "SERVICE_USE", business_date=bdate, ref_type="SERVICE", ref_id=job.id,
                       ref_line_id=item.id, reference=job.job_no, notes=f"{job.brand} {job.model}".strip())
        s.expire(job, ["items"])
        recalc_job(s, job)
        if job.status in ("RECEIVED", "INSPECTION", "WAITING_PARTS"):
            job.status = "IN_PROGRESS"
        audit(s, "JOB_PART_ADDED", "service_job", job.id, {"job_no": job.job_no, "part": p.name, "qty": qty,
                                                          "price": price})
        return item.id


def remove_part(job_id: int, item_id: int) -> None:
    require("services")
    with session_scope() as s:
        job = _load_job(s, job_id)
        _assert_editable(job)
        if job.status in DONE_STATUSES:
            raise BusinessError("Move the job back to 'Repair in progress' before removing parts.")
        item = s.get(ServiceItem, item_id)
        if item is None or item.job_id != job.id or item.status != "ACTIVE":
            raise ValidationError("Part not found on this job.")
        _return_part(s, job, item, "Removed from job")
        recalc_job(s, job)
        audit(s, "JOB_PART_REMOVED", "service_job", job.id, {"job_no": job.job_no, "part": item.product_name,
                                                            "qty": item.quantity})


def _return_part(s, job: ServiceJob, item: ServiceItem, note: str) -> None:
    bdate = current_business_date(s)
    apply_movement(s, s.get(Product, item.product_id), item.quantity, "SERVICE_RETURN", business_date=bdate,
                   ref_type="SERVICE", ref_id=job.id, ref_line_id=item.id, reference=job.job_no,
                   unit_cost=item.unit_cost, notes=note)
    item.status, item.removed_date = "REMOVED", bdate


def set_status(job_id: int, status: str, *, reason: str = "", refund_mode: str = "CASH") -> None:
    require("services")
    if status not in JOB_STATUSES:
        raise ValidationError("Invalid status.")
    with session_scope() as s:
        job = _load_job(s, job_id)
        _assert_editable(job)
        old = job.status
        if old == status:
            return
        bdate = current_business_date(s)
        if status in DONE_STATUSES:
            if not job.completed_date:
                recalc_job(s, job)
                job.completed_date = bdate
                if job.warranty_days:
                    job.warranty_expiry = bdate + timedelta(days=job.warranty_days)
            if status == "DELIVERED":
                if job.paid_amount > job.total_amount:
                    raise BusinessError("The advance is more than the final bill. Use 'Deliver' to refund the excess.")
                job.delivered_at, job.delivered_date = datetime.now().replace(microsecond=0), bdate
        elif status in OPEN_STATUSES:
            if job.completed_date:
                assert_day_open(s, job.completed_date, "reopen this completed job")
                job.completed_date = None
                job.warranty_expiry = None
        elif status == "CANCELLED":
            reason = (reason or "").strip()
            if not reason:
                raise ValidationError("Please enter the reason for cancelling this job.")
            if job.completed_date:
                assert_day_open(s, job.completed_date, "cancel this completed job")
                job.completed_date = None
            for item in [i for i in job.items if i.status == "ACTIVE"]:
                _return_part(s, job, item, "Job cancelled – part returned to stock")
            if job.paid_amount > 0:
                if refund_mode not in PAYMENT_MODES:
                    raise ValidationError("Choose how the advance is refunded.")
                _job_payment(s, job, job.paid_amount, refund_mode, "OUT", notes="Advance refunded – job cancelled")
            job.cancel_reason = reason
            recalc_job(s, job)
        job.status = status
        audit(s, "JOB_STATUS", "service_job", job.id, {"job_no": job.job_no, "from": old, "to": status,
                                                      "reason": reason})


def add_job_payment(job_id: int, amount, mode: str, notes: str = "", client_ref: str | None = None) -> int:
    require("services")
    amount = q2(amount)
    if amount <= 0:
        raise ValidationError("Enter the amount received.")
    if mode not in PAYMENT_MODES:
        raise ValidationError("Choose a payment mode.")
    with session_scope() as s:
        if client_ref and s.scalar(select(Payment.id).where(Payment.client_ref == client_ref)):
            raise DuplicateError("This payment was already recorded.")
        job = _load_job(s, job_id)
        if job.status == "CANCELLED":
            raise BusinessError("This job is cancelled.")
        if job.status in DONE_STATUSES and amount > job.balance:
            raise ValidationError(f"Amount is more than the pending balance (₹{job.balance:,.2f}).")
        pay = _job_payment(s, job, amount, mode, notes=notes, client_ref=client_ref)
        s.flush()
        audit(s, "JOB_PAYMENT", "service_job", job.id, {"job_no": job.job_no, "amount": amount, "mode": mode})
        return pay.id


def deliver_job(job_id: int, collect_amount=0, mode: str = "CASH", refund_mode: str = "CASH") -> dict:
    """Complete (if needed), collect the balance, refund any excess advance and mark delivered."""
    require("services")
    collect = q2(collect_amount)
    with session_scope() as s:
        job = _load_job(s, job_id)
        _assert_editable(job)
        bdate = current_business_date(s)
        if not job.completed_date:
            recalc_job(s, job)
            job.completed_date = bdate
            if job.warranty_days:
                job.warranty_expiry = bdate + timedelta(days=job.warranty_days)
            job.status = "READY"
        if collect < 0 or collect > max(job.balance, Decimal("0.00")):
            raise ValidationError(f"Amount to collect must be between ₹0 and the balance ₹{max(job.balance, 0):,.2f}.")
        if collect > 0:
            if mode not in PAYMENT_MODES:
                raise ValidationError("Choose a payment mode.")
            _job_payment(s, job, collect, mode, notes="Collected at delivery")
        refund = Decimal("0.00")
        if job.paid_amount > job.total_amount:
            refund = job.paid_amount - job.total_amount
            _job_payment(s, job, refund, refund_mode, "OUT", notes="Excess advance refunded at delivery")
        job.status = "DELIVERED"
        job.delivered_at, job.delivered_date = datetime.now().replace(microsecond=0), bdate
        audit(s, "JOB_DELIVERED", "service_job", job.id, {"job_no": job.job_no, "collected": collect,
                                                         "refund": refund, "balance": job.balance})
        return dict(balance=job.balance, refund=refund, total=job.total_amount)


# ---------------------------------------------------------------- queries
def _job_row(j: ServiceJob) -> dict:
    return dict(id=j.id, job_no=j.job_no, received_date=j.received_date, customer_id=j.customer_id,
                customer_name=j.customer_name, customer_phone=j.customer_phone, brand=j.brand, model=j.model,
                device=f"{j.brand} {j.model}".strip(), imei=j.imei, problem=j.problem, technician=j.technician,
                estimated_delivery=j.estimated_delivery, status=j.status, status_label=STATUS_LABELS[j.status],
                labour=j.labour_charge, parts=j.parts_amount, parts_cost=j.parts_cost, discount=j.discount,
                total=j.total_amount, paid=j.paid_amount,
                balance=j.balance if j.status != "CANCELLED" else Decimal("0.00"),
                completed_date=j.completed_date, delivered_date=j.delivered_date,
                overdue=bool(j.estimated_delivery and j.status in OPEN_STATUSES and j.estimated_delivery < date.today()))


def list_jobs(*, statuses: list[str] | None = None, start: date | None = None, end: date | None = None,
              search: str = "", technician: str | None = None, customer_id: int | None = None,
              limit: int = 5000) -> list[dict]:
    with session_scope() as s:
        q = select(ServiceJob).order_by(ServiceJob.id.desc()).limit(limit)
        if statuses:
            q = q.where(ServiceJob.status.in_(statuses))
        if start:
            q = q.where(ServiceJob.received_date >= start)
        if end:
            q = q.where(ServiceJob.received_date <= end)
        if technician:
            q = q.where(ServiceJob.technician == technician)
        if customer_id:
            q = q.where(ServiceJob.customer_id == customer_id)
        if search:
            like = f"%{search.strip()}%"
            q = q.where(or_(ServiceJob.job_no.ilike(like), ServiceJob.customer_name.ilike(like),
                            ServiceJob.customer_phone.like(like), ServiceJob.imei.like(like),
                            ServiceJob.model.ilike(like), ServiceJob.brand.ilike(like)))
        return [_job_row(j) for j in s.scalars(q)]


def status_counts() -> dict:
    with session_scope() as s:
        rows = s.execute(select(ServiceJob.status, func.count(ServiceJob.id)).group_by(ServiceJob.status)).all()
        return {st: n for st, n in rows}


def get_job(job_id: int) -> dict:
    with session_scope() as s:
        j = _load_job(s, job_id)
        d = _job_row(j)
        cust = s.get(Customer, j.customer_id)
        d.update(accessories=j.accessories, diagnosis=j.diagnosis, notes=j.notes, warranty_days=j.warranty_days,
                 warranty_expiry=j.warranty_expiry, cancel_reason=j.cancel_reason, received_at=j.received_at,
                 delivered_at=j.delivered_at, labour_taxable=j.labour_taxable, labour_gst=j.labour_gst,
                 parts_taxable=j.parts_taxable, parts_gst=j.parts_gst, customer_address=cust.address if cust else "")
        d["items"] = [dict(id=i.id, product_id=i.product_id, product_name=i.product_name, quantity=i.quantity,
                           unit_price=i.unit_price, amount=i.line_amount, unit_cost=i.unit_cost, cost=i.cost_total,
                           status=i.status, added_date=i.added_date) for i in j.items]
        pays = s.scalars(select(Payment).where(Payment.ref_type == "SERVICE", Payment.ref_id == j.id,
                                               Payment.status == "ACTIVE").order_by(Payment.id)).all()
        d["payments"] = [dict(date=p.business_date, created_at=p.created_at, direction=p.direction, mode=p.mode,
                              amount=p.amount, kind=p.kind, notes=p.notes) for p in pays]
        return d


def technicians() -> list[str]:
    names = list(settings.get("technicians") or [])
    with session_scope() as s:
        for (t,) in s.execute(select(ServiceJob.technician).distinct()):
            if t and t not in names:
                names.append(t)
    return names
