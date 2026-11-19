"""Customers & suppliers: balances, ledgers and payment collection."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import or_, select, text

from ..db.base import D, q2, session_scope
from ..db.models import Customer, Payment, Purchase, Sale, SaleReturn, ServiceJob, Supplier
from .audit import audit
from .auth import current_user, require
from .business_day import current_business_date
from .errors import DuplicateError, NotFoundError, ValidationError
from .repairs import clean_phone
from .sales import MODE_LABELS, PAYMENT_MODES

ZERO = Decimal("0.00")


def P(v) -> Decimal:
    """Integer paise (raw SQL) -> rupees."""
    return (Decimal(int(v or 0)) / 100).quantize(Decimal("0.01"))


KIND_LABELS = {"SALE_RECEIPT": "Payment received", "DUE_COLLECTION": "Due collected", "SERVICE_ADVANCE": "Service advance",
               "SERVICE_RECEIPT": "Service payment", "OPENING_DUE": "Old dues collected", "REFUND": "Refund given",
               "SERVICE_REFUND": "Service refund", "EXCHANGE_CREDIT": "Exchange adjustment",
               "SUPPLIER_PAYMENT": "Paid to supplier"}


# ================================================================ customers
def _customer_aggregates(s) -> dict[int, dict]:
    agg: dict[int, dict] = {}

    def g(cid):
        return agg.setdefault(cid, dict(sales_due=ZERO, purchases=ZERO, service_due=ZERO, advances=ZERO,
                                        service_total=ZERO, opening_paid=ZERO, last_sale=None, last_job=None))
    for cid, due, purch, last in s.execute(text(
            "SELECT customer_id, SUM(grand_total - returned_amount - paid_amount + refunded_amount), "
            "SUM(grand_total - returned_amount), MAX(business_date) FROM sales WHERE status='COMPLETED' "
            "GROUP BY customer_id")):
        a = g(cid)
        a["sales_due"], a["purchases"], a["last_sale"] = P(due), P(purch), last
    for cid, due, adv, tot, last in s.execute(text(
            "SELECT customer_id, "
            "SUM(CASE WHEN completed_date IS NOT NULL AND status!='CANCELLED' THEN total_amount - paid_amount ELSE 0 END), "
            "SUM(CASE WHEN completed_date IS NULL AND status!='CANCELLED' THEN paid_amount ELSE 0 END), "
            "SUM(CASE WHEN completed_date IS NOT NULL AND status!='CANCELLED' THEN total_amount ELSE 0 END), "
            "MAX(received_date) FROM service_jobs GROUP BY customer_id")):
        a = g(cid)
        a["service_due"], a["advances"], a["service_total"], a["last_job"] = P(due), P(adv), P(tot), last
    for cid, paid in s.execute(text(
            "SELECT customer_id, SUM(amount) FROM payments WHERE status='ACTIVE' AND ref_type='OPENING' "
            "AND direction='IN' AND customer_id IS NOT NULL GROUP BY customer_id")):
        g(cid)["opening_paid"] = P(paid)
    return agg


def _balance_from(c: Customer, a: dict | None) -> Decimal:
    a = a or {}
    return (c.opening_balance - a.get("opening_paid", ZERO) + a.get("sales_due", ZERO)
            + a.get("service_due", ZERO) - a.get("advances", ZERO))


def customer_balance(customer_id: int) -> Decimal:
    with session_scope() as s:
        c = s.get(Customer, customer_id)
        if c is None:
            raise NotFoundError("Customer not found.")
        return _balance_from(c, _customer_aggregates(s).get(customer_id))


def list_customers(search: str = "", with_dues_only: bool = False, include_walk_in: bool = False) -> list[dict]:
    with session_scope() as s:
        agg = _customer_aggregates(s)
        q = select(Customer).where(Customer.is_active.is_(True)).order_by(Customer.name)
        if not include_walk_in:
            q = q.where(Customer.is_walk_in.is_(False))
        if search:
            like = f"%{search.strip()}%"
            q = q.where(or_(Customer.name.ilike(like), Customer.phone.like(like), Customer.address.ilike(like)))
        out = []
        for c in s.scalars(q):
            a = agg.get(c.id, {})
            bal = _balance_from(c, a)
            if with_dues_only and bal <= 0:
                continue
            last = max([x for x in (a.get("last_sale"), a.get("last_job")) if x] or [None], key=lambda v: str(v or ""))
            out.append(dict(id=c.id, name=c.name, phone=c.phone, address=c.address, email=c.email, gstin=c.gstin,
                            opening_balance=c.opening_balance, notes=c.notes, is_walk_in=c.is_walk_in,
                            total_purchases=a.get("purchases", ZERO), total_service=a.get("service_total", ZERO),
                            pending=max(bal, ZERO), credit=max(-bal, ZERO), balance=bal, last_visit=last))
        return out


def total_receivables() -> dict:
    rows = list_customers(include_walk_in=True)
    return dict(total=sum((r["pending"] for r in rows), ZERO), count=sum(1 for r in rows if r["pending"] > 0),
                advances=sum((r["credit"] for r in rows), ZERO))


def search_customers(text_: str, limit: int = 20) -> list[dict]:
    with session_scope() as s:
        like = f"%{(text_ or '').strip()}%"
        q = select(Customer).where(Customer.is_active.is_(True), Customer.is_walk_in.is_(False),
                                   or_(Customer.name.ilike(like), Customer.phone.like(like))) \
            .order_by(Customer.name).limit(limit)
        return [dict(id=c.id, name=c.name, phone=c.phone, address=c.address) for c in s.scalars(q)]


def _clean_customer(s, data: dict, cid: int | None) -> dict:
    name = (data.get("name") or "").strip()
    if not name:
        raise ValidationError("Customer name is required.")
    phone = clean_phone(data.get("phone", ""))
    if phone:
        q = select(Customer).where(Customer.phone == phone, Customer.is_walk_in.is_(False))
        if cid:
            q = q.where(Customer.id != cid)
        other = s.scalar(q)
        if other is not None:
            raise DuplicateError(f"Phone {phone} already belongs to customer '{other.name}'.")
    ob = q2(data.get("opening_balance") or 0)
    if ob < 0:
        raise ValidationError("Opening balance cannot be negative.")
    return dict(name=name, phone=phone, address=(data.get("address") or "").strip(),
                email=(data.get("email") or "").strip(), gstin=(data.get("gstin") or "").strip().upper(),
                notes=(data.get("notes") or "").strip(), opening_balance=ob)


def create_customer(data: dict) -> int:
    require("customers")
    with session_scope() as s:
        c = Customer(**_clean_customer(s, data, None))
        s.add(c)
        s.flush()
        audit(s, "CUSTOMER_CREATED", "customer", c.id, {"name": c.name, "phone": c.phone})
        return c.id


def update_customer(customer_id: int, data: dict) -> None:
    require("customers")
    with session_scope() as s:
        c = s.get(Customer, customer_id)
        if c is None or c.is_walk_in:
            raise ValidationError("This customer cannot be edited.")
        d = _clean_customer(s, {**dict(name=c.name, phone=c.phone, address=c.address, email=c.email, gstin=c.gstin,
                                       notes=c.notes, opening_balance=c.opening_balance), **data}, customer_id)
        before = dict(name=c.name, phone=c.phone, opening_balance=c.opening_balance)
        for k, v in d.items():
            setattr(c, k, v)
        audit(s, "CUSTOMER_UPDATED", "customer", c.id, {"before": before,
                                                       "after": dict(name=c.name, phone=c.phone,
                                                                     opening_balance=c.opening_balance)})


def get_customer(customer_id: int) -> dict:
    with session_scope() as s:
        c = s.get(Customer, customer_id)
        if c is None:
            raise NotFoundError("Customer not found.")
        return dict(id=c.id, name=c.name, phone=c.phone, address=c.address, email=c.email, gstin=c.gstin,
                    notes=c.notes, opening_balance=c.opening_balance, is_walk_in=c.is_walk_in)


def _outstanding(s, c: Customer) -> list[dict]:
    docs = []
    opening_paid = P(s.execute(text("SELECT SUM(amount) FROM payments WHERE status='ACTIVE' AND ref_type='OPENING' "
                                    "AND direction='IN' AND customer_id=:c"), {"c": c.id}).scalar())
    op_due = c.opening_balance - opening_paid
    if op_due > 0:
        docs.append(dict(kind="OPENING", obj=c, due=op_due, ref="Old dues", date=None))
    for sale in s.scalars(select(Sale).where(Sale.customer_id == c.id, Sale.status == "COMPLETED").order_by(Sale.id)):
        if sale.balance_due > 0:
            docs.append(dict(kind="SALE", obj=sale, due=sale.balance_due, ref=sale.invoice_no, date=sale.business_date))
    for job in s.scalars(select(ServiceJob).where(ServiceJob.customer_id == c.id, ServiceJob.status != "CANCELLED",
                                                  ServiceJob.completed_date.is_not(None)).order_by(ServiceJob.completed_date, ServiceJob.id)):
        if job.balance > 0:
            docs.append(dict(kind="SERVICE", obj=job, due=job.balance, ref=job.job_no, date=job.completed_date))
    return docs


def outstanding_documents(customer_id: int) -> list[dict]:
    with session_scope() as s:
        c = s.get(Customer, customer_id)
        if c is None:
            raise NotFoundError("Customer not found.")
        return [dict(kind=d["kind"], ref=d["ref"], date=d["date"], due=d["due"]) for d in _outstanding(s, c)]


def collect_customer_payment(customer_id: int, amount, mode: str, notes: str = "", client_ref: str | None = None) -> list[dict]:
    """Collect a lump sum and settle the oldest dues first (FIFO)."""
    require("collect_payment")
    amount = q2(amount)
    if amount <= 0:
        raise ValidationError("Enter the amount received.")
    if mode not in PAYMENT_MODES:
        raise ValidationError("Choose a payment mode.")
    with session_scope() as s:
        if client_ref and s.scalar(select(Payment.id).where(Payment.client_ref.like(f"{client_ref}%"))):
            raise DuplicateError("This payment was already recorded.")
        c = s.get(Customer, customer_id)
        if c is None:
            raise NotFoundError("Customer not found.")
        docs = _outstanding(s, c)
        total_due = sum((d["due"] for d in docs), ZERO)
        if amount > total_due:
            raise ValidationError(f"Amount is more than the total pending balance (₹{total_due:,.2f}).")
        bdate = current_business_date(s)
        left, out = amount, []
        for i, d in enumerate(docs):
            if left <= 0:
                break
            part = min(left, d["due"])
            ref_type = {"OPENING": "OPENING", "SALE": "SALE", "SERVICE": "SERVICE"}[d["kind"]]
            s.add(Payment(client_ref=f"{client_ref}:{i}" if client_ref else None, business_date=bdate, direction="IN",
                          mode=mode, amount=part, kind="OPENING_DUE" if d["kind"] == "OPENING" else "DUE_COLLECTION",
                          customer_id=c.id, ref_type=ref_type, ref_id=c.id if d["kind"] == "OPENING" else d["obj"].id,
                          reference=d["ref"], notes=notes, user_id=current_user().id))
            if d["kind"] in ("SALE", "SERVICE"):
                d["obj"].paid_amount = d["obj"].paid_amount + part
            out.append(dict(ref=d["ref"], amount=part))
            left -= part
        s.flush()
        audit(s, "CUSTOMER_PAYMENT", "customer", c.id, {"name": c.name, "amount": amount, "mode": mode,
                                                       "allocations": out})
        return out


def customer_ledger(customer_id: int) -> dict:
    with session_scope() as s:
        c = s.get(Customer, customer_id)
        if c is None:
            raise NotFoundError("Customer not found.")
        entries = []
        if c.opening_balance:
            entries.append((date.min, c.created_at, 0, "Opening balance (old dues)", "", c.opening_balance, ZERO))
        for sale in s.scalars(select(Sale).where(Sale.customer_id == c.id, Sale.status == "COMPLETED")):
            entries.append((sale.business_date, sale.created_at, 1, f"Sale invoice ({len(sale.items)} item/s)",
                            sale.invoice_no, sale.grand_total, ZERO))
        for r, inv in s.execute(select(SaleReturn, Sale.invoice_no).join(Sale, SaleReturn.sale_id == Sale.id)
                                .where(Sale.customer_id == c.id)):
            entries.append((r.business_date, r.created_at, 2, f"Return against {inv}", r.return_no, ZERO, r.total_value))
        for j in s.scalars(select(ServiceJob).where(ServiceJob.customer_id == c.id, ServiceJob.status != "CANCELLED",
                                                    ServiceJob.completed_date.is_not(None))):
            entries.append((j.completed_date, j.received_at, 1, f"Repair {j.brand} {j.model}".strip(), j.job_no,
                            j.total_amount, ZERO))
        for p in s.scalars(select(Payment).where(Payment.customer_id == c.id, Payment.status == "ACTIVE")):
            label = f"{KIND_LABELS.get(p.kind, p.kind)} ({MODE_LABELS.get(p.mode, p.mode)})"
            if p.direction == "IN":
                entries.append((p.business_date, p.created_at, 3, label, p.reference, ZERO, p.amount))
            else:
                entries.append((p.business_date, p.created_at, 3, label, p.reference, p.amount, ZERO))
        entries.sort(key=lambda e: (e[0], e[1] or datetime.min, e[2]))
        bal, rows = ZERO, []
        tot_dr = tot_cr = ZERO
        for d, _, _, part, ref, dr, cr in entries:
            bal += dr - cr
            tot_dr += dr
            tot_cr += cr
            rows.append(dict(date=None if d == date.min else d, particulars=part, ref=ref, debit=dr, credit=cr, balance=bal))
        return dict(customer=dict(id=c.id, name=c.name, phone=c.phone, address=c.address), rows=rows,
                    total_debit=tot_dr, total_credit=tot_cr, balance=bal)


# ================================================================ suppliers
def _supplier_aggs(s) -> dict[int, dict]:
    agg: dict[int, dict] = {}
    for sid, tot, n, last in s.execute(text(
            "SELECT supplier_id, SUM(total), COUNT(id), MAX(business_date) FROM purchases WHERE status='ACTIVE' "
            "GROUP BY supplier_id")):
        agg.setdefault(sid, {}).update(purchases=P(tot), count=n, last=last)
    for sid, paid in s.execute(text(
            "SELECT supplier_id, SUM(amount) FROM payments WHERE status='ACTIVE' AND direction='OUT' "
            "AND kind='SUPPLIER_PAYMENT' AND supplier_id IS NOT NULL GROUP BY supplier_id")):
        agg.setdefault(sid, {})["paid"] = P(paid)
    return agg


def list_suppliers(search: str = "") -> list[dict]:
    with session_scope() as s:
        agg = _supplier_aggs(s)
        q = select(Supplier).where(Supplier.is_active.is_(True)).order_by(Supplier.name)
        if search:
            like = f"%{search}%"
            q = q.where(or_(Supplier.name.ilike(like), Supplier.phone.like(like), Supplier.contact_person.ilike(like)))
        out = []
        for x in s.scalars(q):
            a = agg.get(x.id, {})
            purchases, paid = a.get("purchases", ZERO), a.get("paid", ZERO)
            due = x.opening_balance + purchases - paid
            out.append(dict(id=x.id, name=x.name, contact_person=x.contact_person, phone=x.phone, email=x.email,
                            address=x.address, gstin=x.gstin, opening_balance=x.opening_balance, notes=x.notes,
                            total_purchases=purchases, paid=paid, due=due, purchase_count=a.get("count", 0),
                            last_purchase=a.get("last")))
        return out


def total_payables() -> Decimal:
    return sum((max(r["due"], ZERO) for r in list_suppliers()), ZERO)


def _clean_supplier(data: dict) -> dict:
    name = (data.get("name") or "").strip()
    if not name:
        raise ValidationError("Supplier name is required.")
    ob = q2(data.get("opening_balance") or 0)
    if ob < 0:
        raise ValidationError("Opening balance cannot be negative.")
    return dict(name=name, contact_person=(data.get("contact_person") or "").strip(), phone=clean_phone(data.get("phone", "")),
                email=(data.get("email") or "").strip(), address=(data.get("address") or "").strip(),
                gstin=(data.get("gstin") or "").strip().upper(), notes=(data.get("notes") or "").strip(),
                opening_balance=ob)


def create_supplier(data: dict) -> int:
    require("suppliers")
    with session_scope() as s:
        d = _clean_supplier(data)
        if s.scalar(select(Supplier.id).where(Supplier.name == d["name"], Supplier.is_active.is_(True))):
            raise DuplicateError(f"Supplier '{d['name']}' already exists.")
        x = Supplier(**d)
        s.add(x)
        s.flush()
        audit(s, "SUPPLIER_CREATED", "supplier", x.id, {"name": x.name})
        return x.id


def update_supplier(supplier_id: int, data: dict) -> None:
    require("suppliers")
    with session_scope() as s:
        x = s.get(Supplier, supplier_id)
        if x is None:
            raise NotFoundError("Supplier not found.")
        d = _clean_supplier({**dict(name=x.name, contact_person=x.contact_person, phone=x.phone, email=x.email,
                                    address=x.address, gstin=x.gstin, notes=x.notes,
                                    opening_balance=x.opening_balance), **data})
        for k, v in d.items():
            setattr(x, k, v)
        audit(s, "SUPPLIER_UPDATED", "supplier", x.id, {"name": x.name})


def pay_supplier(supplier_id: int, amount, mode: str, notes: str = "", purchase_id: int | None = None,
                 client_ref: str | None = None) -> list[dict]:
    require("suppliers")
    amount = q2(amount)
    if amount <= 0:
        raise ValidationError("Enter the amount paid.")
    if mode not in PAYMENT_MODES:
        raise ValidationError("Choose a payment mode.")
    with session_scope() as s:
        if client_ref and s.scalar(select(Payment.id).where(Payment.client_ref.like(f"{client_ref}%"))):
            raise DuplicateError("This payment was already recorded.")
        x = s.get(Supplier, supplier_id)
        if x is None:
            raise NotFoundError("Supplier not found.")
        q = select(Purchase).where(Purchase.supplier_id == x.id, Purchase.status == "ACTIVE").order_by(Purchase.id)
        if purchase_id:
            q = q.where(Purchase.id == purchase_id)
        docs = [(p, p.total - p.paid_amount) for p in s.scalars(q) if p.total - p.paid_amount > 0]
        paid_opening = P(s.execute(text("SELECT SUM(amount) FROM payments WHERE status='ACTIVE' AND direction='OUT' "
                                        "AND ref_type='OPENING' AND supplier_id=:s"), {"s": x.id}).scalar())
        op_due = x.opening_balance - paid_opening if not purchase_id else ZERO
        total_due = op_due + sum((d for _, d in docs), ZERO)
        if amount > total_due:
            raise ValidationError(f"Amount is more than the amount due (₹{total_due:,.2f}).")
        bdate = current_business_date(s)
        left, out, i = amount, [], 0
        if op_due > 0:
            part = min(left, op_due)
            s.add(Payment(client_ref=f"{client_ref}:{i}" if client_ref else None, business_date=bdate, direction="OUT",
                          mode=mode, amount=part, kind="SUPPLIER_PAYMENT", supplier_id=x.id, ref_type="OPENING",
                          ref_id=x.id, reference="Opening balance", notes=notes, user_id=current_user().id))
            out.append(dict(ref="Opening balance", amount=part))
            left -= part
            i += 1
        for p, due in docs:
            if left <= 0:
                break
            part = min(left, due)
            s.add(Payment(client_ref=f"{client_ref}:{i}" if client_ref else None, business_date=bdate, direction="OUT",
                          mode=mode, amount=part, kind="SUPPLIER_PAYMENT", supplier_id=x.id, ref_type="PURCHASE",
                          ref_id=p.id, reference=p.purchase_no, notes=notes, user_id=current_user().id))
            p.paid_amount = p.paid_amount + part
            out.append(dict(ref=p.purchase_no, amount=part))
            left -= part
            i += 1
        s.flush()
        audit(s, "SUPPLIER_PAYMENT", "supplier", x.id, {"name": x.name, "amount": amount, "mode": mode,
                                                       "allocations": out})
        return out


def supplier_ledger(supplier_id: int) -> dict:
    with session_scope() as s:
        x = s.get(Supplier, supplier_id)
        if x is None:
            raise NotFoundError("Supplier not found.")
        entries = []
        if x.opening_balance:
            entries.append((date.min, x.created_at, "Opening balance", "", ZERO, x.opening_balance))
        for p in s.scalars(select(Purchase).where(Purchase.supplier_id == x.id, Purchase.status == "ACTIVE")):
            entries.append((p.business_date, p.created_at, f"Purchase {p.supplier_invoice_no}".strip(), p.purchase_no,
                            ZERO, p.total))
        for pay in s.scalars(select(Payment).where(Payment.supplier_id == x.id, Payment.status == "ACTIVE")):
            amt_dr = pay.amount if pay.direction == "OUT" else ZERO
            amt_cr = pay.amount if pay.direction == "IN" else ZERO
            entries.append((pay.business_date, pay.created_at, f"Payment ({MODE_LABELS.get(pay.mode, pay.mode)})",
                            pay.reference, amt_dr, amt_cr))
        entries.sort(key=lambda e: (e[0], e[1] or datetime.min))
        bal, rows = ZERO, []
        for d, _, part, ref, dr, cr in entries:
            bal += cr - dr
            rows.append(dict(date=None if d == date.min else d, particulars=part, ref=ref, debit=dr, credit=cr, balance=bal))
        return dict(supplier=dict(id=x.id, name=x.name, phone=x.phone), rows=rows, balance=bal)
