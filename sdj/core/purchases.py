"""Purchase entry (stock in at landed cost) and supplier payments."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from ..db.base import D, q2, q4, session_scope
from ..db.models import ImeiRecord, Payment, Product, Purchase, PurchaseItem, Supplier
from . import settings
from .audit import audit
from .auth import current_user, require
from .business_day import assert_day_open, current_business_date
from .calc import allocate
from .errors import BusinessError, DuplicateError, NotFoundError, ValidationError
from .inventory import _validate_units, add_imei_units, apply_movement
from .numbering import next_simple
from .sales import PAYMENT_MODES


def compute_purchase(lines: list[dict], discount=0, additional_charges=0, gst_enabled: bool | None = None) -> dict:
    """lines: [{quantity, unit_price (excl. GST), gst_rate}] -> totals and landed unit costs."""
    if gst_enabled is None:
        gst_enabled = bool(settings.get("gst_enabled"))
    discount, charges = q2(discount), q2(additional_charges)
    bases, gsts, totals = [], [], []
    for ln in lines:
        base = q2(D(ln["quantity"]) * D(ln["unit_price"]))
        gst = q2(base * D(ln.get("gst_rate") or 0) / 100)
        bases.append(base)
        gsts.append(gst)
        totals.append(base + gst)
    subtotal, gst_total = sum(bases, Decimal("0.00")), sum(gsts, Decimal("0.00"))
    total = subtotal + gst_total - discount + charges
    disc_shares = allocate(discount, totals)
    chg_shares = allocate(charges, totals)
    landed = []
    for ln, base, gst, ds, cs in zip(lines, bases, gsts, disc_shares, chg_shares):
        cost_basis = base + (Decimal("0") if gst_enabled else gst) - ds + cs
        qty = int(ln["quantity"]) or 1
        landed.append(q4(cost_basis / qty))
    return dict(subtotal=subtotal, gst_total=gst_total, discount=discount, charges=charges, total=total,
                line_bases=bases, line_gst=gsts, line_totals=totals, landed=landed)


def create_purchase(supplier_id: int, lines: list[dict], *, supplier_invoice_no: str = "",
                    purchase_date: date | None = None, discount=0, additional_charges=0, paid_amount=0,
                    payment_mode: str = "CASH", notes: str = "", client_ref: str | None = None) -> dict:
    """lines: [{product_id, quantity, unit_price, gst_rate, imei_units: [{imei1, imei2, serial_no}]}]"""
    require("purchases")
    if not lines:
        raise ValidationError("Add at least one product to the purchase.")
    try:
        with session_scope() as s:
            if client_ref and s.scalar(select(Purchase.id).where(Purchase.client_ref == client_ref)):
                raise DuplicateError("This purchase was already saved (duplicate prevented).")
            sup = s.get(Supplier, supplier_id) if supplier_id else None
            if sup is None:
                raise ValidationError("Please select the supplier.")
            inv_no = (supplier_invoice_no or "").strip()
            if inv_no and s.scalar(select(Purchase.id).where(Purchase.supplier_id == sup.id,
                                                             Purchase.supplier_invoice_no == inv_no,
                                                             Purchase.status == "ACTIVE")):
                raise DuplicateError(f"Supplier invoice {inv_no} from {sup.name} is already entered.")
            prods = []
            for ln in lines:
                p = s.get(Product, ln.get("product_id"))
                if p is None:
                    raise ValidationError("A product in the purchase was not found.")
                q = int(ln.get("quantity") or 0)
                if q <= 0:
                    raise ValidationError(f"Quantity for '{p.name}' must be at least 1.")
                if D(ln.get("unit_price")) < 0 or D(ln.get("gst_rate") or 0) < 0:
                    raise ValidationError(f"Price/GST for '{p.name}' cannot be negative.")
                if p.track_imei:
                    ln["imei_units"] = _validate_units(s, ln.get("imei_units") or [], q)
                prods.append(p)
            # IMEIs must be unique across the whole purchase too
            seen = set()
            for ln in lines:
                for u in ln.get("imei_units") or []:
                    for v in filter(None, (u["imei1"], u.get("imei2"))):
                        if v in seen:
                            raise ValidationError(f"IMEI {v} is entered more than once in this purchase.")
                        seen.add(v)
            calc = compute_purchase(lines, discount, additional_charges)
            if calc["discount"] < 0 or calc["charges"] < 0:
                raise ValidationError("Discount and additional charges cannot be negative.")
            if calc["discount"] > calc["subtotal"] + calc["gst_total"]:
                raise ValidationError("Discount cannot be more than the purchase value.")
            paid = q2(paid_amount)
            if paid < 0 or paid > calc["total"]:
                raise ValidationError("Amount paid must be between zero and the purchase total.")
            if paid > 0 and payment_mode not in PAYMENT_MODES:
                raise ValidationError("Choose the payment mode.")
            bdate = current_business_date(s)
            pdate = purchase_date or bdate
            user = current_user()
            pur = Purchase(purchase_no=next_simple(s, "purchase_no", settings.get("purchase_prefix") or "PUR"),
                           client_ref=client_ref, supplier_id=sup.id, supplier_invoice_no=inv_no, purchase_date=pdate,
                           business_date=bdate, subtotal=calc["subtotal"], gst_amount=calc["gst_total"],
                           discount=calc["discount"], additional_charges=calc["charges"], total=calc["total"],
                           paid_amount=paid, status="ACTIVE", notes=(notes or "").strip(), created_by=user.id)
            s.add(pur)
            s.flush()
            for ln, p, base, gst, ltotal, landed in zip(lines, prods, calc["line_bases"], calc["line_gst"],
                                                        calc["line_totals"], calc["landed"]):
                q = int(ln["quantity"])
                units = ln.get("imei_units") or []
                item = PurchaseItem(purchase_id=pur.id, product_id=p.id, product_name=p.name, quantity=q,
                                    unit_price=q2(ln["unit_price"]), gst_rate=q2(ln.get("gst_rate") or 0),
                                    gst_amount=gst, line_total=ltotal, landed_unit_cost=landed,
                                    imei_list="\n".join(u["imei1"] for u in units))
                s.add(item)
                s.flush()
                if p.track_imei:
                    add_imei_units(s, p, units, source="PURCHASE", cost=landed, purchase_date=pdate,
                                   purchase_item_id=item.id)
                apply_movement(s, p, q, "PURCHASE", business_date=bdate, ref_type="PURCHASE", ref_id=pur.id,
                               ref_line_id=item.id, reference=pur.purchase_no, unit_cost=landed,
                               notes=f"{sup.name} {inv_no}".strip())
                p.purchase_price = q2(ln["unit_price"])
                p.purchase_date = pdate
                if not p.supplier_id:
                    p.supplier_id = sup.id
            if paid > 0:
                s.add(Payment(client_ref=f"{client_ref}:pay" if client_ref else None, business_date=bdate,
                              direction="OUT", mode=payment_mode, amount=paid, kind="SUPPLIER_PAYMENT",
                              supplier_id=sup.id, ref_type="PURCHASE", ref_id=pur.id, reference=pur.purchase_no,
                              user_id=user.id))
            s.flush()
            audit(s, "PURCHASE_CREATED", "purchase", pur.id, {"purchase_no": pur.purchase_no, "supplier": sup.name,
                                                             "total": pur.total, "paid": paid})
            return dict(id=pur.id, purchase_no=pur.purchase_no, total=pur.total, paid=paid, balance=pur.total - paid)
    except IntegrityError as exc:
        raise DuplicateError("This purchase was already saved (duplicate prevented).") from exc


def cancel_purchase(purchase_id: int, reason: str) -> None:
    require("purchases")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Please enter the reason for cancelling this purchase.")
    with session_scope() as s:
        pur = s.get(Purchase, purchase_id)
        if pur is None:
            raise NotFoundError("Purchase not found.")
        if pur.status != "ACTIVE":
            raise BusinessError("This purchase is already cancelled.")
        assert_day_open(s, pur.business_date, "cancel this purchase")
        pays = s.scalars(select(Payment).where(Payment.ref_type == "PURCHASE", Payment.ref_id == pur.id,
                                               Payment.status == "ACTIVE")).all()
        for p in pays:
            assert_day_open(s, p.business_date, "cancel this purchase (a payment was made on a closed day)")
        for it in pur.items:
            prod = s.get(Product, it.product_id)
            if prod.quantity < it.quantity:
                raise BusinessError(f"Cannot cancel: {it.quantity} x '{prod.name}' were received but only "
                                    f"{prod.quantity} are in stock now (some were already sold/used).")
            if prod.track_imei:
                recs = s.scalars(select(ImeiRecord).where(ImeiRecord.purchase_item_id == it.id)).all()
                if any(r.status != "IN_STOCK" for r in recs):
                    raise BusinessError(f"Cannot cancel: a phone from this purchase ('{prod.name}') was already sold.")
                for r in recs:
                    r.status = "REMOVED"
                    r.notes = (r.notes + "\n" if r.notes else "") + f"Purchase {pur.purchase_no} cancelled"
            apply_movement(s, prod, -it.quantity, "PURCHASE_CANCEL", business_date=pur.business_date,
                           ref_type="PURCHASE", ref_id=pur.id, ref_line_id=it.id, reference=pur.purchase_no,
                           unit_cost=it.landed_unit_cost, notes=f"Purchase cancelled: {reason}")
        now = datetime.now().replace(microsecond=0)
        for p in pays:
            p.status, p.voided_at, p.void_reason = "VOID", now, f"Purchase cancelled: {reason}"
        pur.status, pur.paid_amount, pur.cancelled_at, pur.cancel_reason = "CANCELLED", Decimal("0.00"), now, reason
        audit(s, "PURCHASE_CANCELLED", "purchase", pur.id, {"purchase_no": pur.purchase_no, "reason": reason})


def list_purchases(start: date | None = None, end: date | None = None, search: str = "",
                   supplier_id: int | None = None, status: str | None = None) -> list[dict]:
    with session_scope() as s:
        q = select(Purchase, Supplier.name).join(Supplier).order_by(Purchase.id.desc())
        if start:
            q = q.where(Purchase.business_date >= start)
        if end:
            q = q.where(Purchase.business_date <= end)
        if supplier_id:
            q = q.where(Purchase.supplier_id == supplier_id)
        if status:
            q = q.where(Purchase.status == status)
        if search:
            like = f"%{search}%"
            q = q.where(or_(Purchase.purchase_no.ilike(like), Purchase.supplier_invoice_no.ilike(like),
                            Supplier.name.ilike(like)))
        return [dict(id=p.id, purchase_no=p.purchase_no, business_date=p.business_date, purchase_date=p.purchase_date,
                     supplier=name, supplier_id=p.supplier_id, invoice_no=p.supplier_invoice_no, items=len(p.items),
                     total=p.total, paid=p.paid_amount, balance=(p.total - p.paid_amount) if p.status == "ACTIVE" else Decimal("0.00"),
                     status=p.status) for p, name in s.execute(q)]


def get_purchase(purchase_id: int) -> dict:
    with session_scope() as s:
        p = s.get(Purchase, purchase_id)
        if p is None:
            raise NotFoundError("Purchase not found.")
        pays = s.scalars(select(Payment).where(Payment.ref_type == "PURCHASE", Payment.ref_id == p.id)).all()
        return dict(id=p.id, purchase_no=p.purchase_no, supplier=p.supplier.name, supplier_id=p.supplier_id,
                    invoice_no=p.supplier_invoice_no, purchase_date=p.purchase_date, business_date=p.business_date,
                    subtotal=p.subtotal, gst=p.gst_amount, discount=p.discount, charges=p.additional_charges,
                    total=p.total, paid=p.paid_amount, balance=p.total - p.paid_amount, status=p.status,
                    notes=p.notes, cancel_reason=p.cancel_reason,
                    lines=[dict(product=i.product_name, quantity=i.quantity, unit_price=i.unit_price,
                                gst_rate=i.gst_rate, gst=i.gst_amount, total=i.line_total,
                                landed=i.landed_unit_cost, imeis=i.imei_list) for i in p.items],
                    payments=[dict(date=x.business_date, mode=x.mode, amount=x.amount, status=x.status) for x in pays])
