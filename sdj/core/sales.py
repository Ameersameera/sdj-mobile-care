"""Billing / POS and quick sales with automatic stock deduction."""
from __future__ import annotations

import calendar
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from ..db.base import D, q2, session_scope
from ..db.models import Customer, ImeiRecord, Payment, Product, Sale, SaleItem, SaleReturn
from . import settings
from .audit import audit
from .auth import current_user, require
from .business_day import assert_day_open, current_business_date
from .calc import LineIn, compute_bill
from .errors import BusinessError, DuplicateError, NotFoundError, PermissionDenied, ValidationError
from .inventory import apply_movement
from .numbering import next_invoice_no

PAYMENT_MODES = ["CASH", "UPI", "CARD", "OTHER"]
MODE_LABELS = {"CASH": "Cash", "UPI": "UPI", "CARD": "Card", "OTHER": "Other", "EXCHANGE": "Exchange credit"}


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


@dataclass
class CartItem:
    product_id: int
    quantity: int
    unit_price: Decimal
    discount: Decimal = Decimal("0")
    imei_record_id: int | None = None
    imei_text: str = ""
    extra: dict = field(default_factory=dict)


def walk_in_customer_id(session) -> int:
    cid = session.scalar(select(Customer.id).where(Customer.is_walk_in.is_(True)).order_by(Customer.id))
    if cid is None:
        c = Customer(name="Walk-in Customer", is_walk_in=True)
        session.add(c)
        session.flush()
        cid = c.id
    return cid


def tax_flags() -> dict:
    return dict(gst_enabled=bool(settings.get("gst_enabled")),
                inclusive=bool(settings.get("prices_include_gst")),
                round_off=bool(settings.get("round_off")))


def preview_bill(lines: list[LineIn], bill_discount=0):
    """Same maths the save uses – for live totals in the billing screen."""
    return compute_bill(lines, bill_discount, **tax_flags())


def _create_sale(s, items: list[CartItem], *, customer_id: int | None, bill_discount=0,
                 payments: dict | None = None, pay_full_mode: str | None = None, notes: str = "",
                 client_ref: str | None = None, sale_type: str = "BILL", invoice_no: str | None = None,
                 business_date: date | None = None) -> Sale:
    if not items:
        raise ValidationError("Add at least one product to the bill.")
    if client_ref:
        dup = s.scalar(select(Sale).where(Sale.client_ref == client_ref))
        if dup is not None:
            raise DuplicateError(f"This bill is already saved as invoice {dup.invoice_no}. It was not saved twice.")
    bdate = business_date or current_business_date(s)
    cust = s.get(Customer, customer_id) if customer_id else None
    if cust is None:
        cust = s.get(Customer, walk_in_customer_id(s))

    products: dict[int, Product] = {}
    needed: dict[int, int] = defaultdict(int)
    imei_recs: dict[int, ImeiRecord] = {}
    for it in items:
        p = products.get(it.product_id) or s.get(Product, it.product_id)
        if p is None or not p.is_active:
            raise ValidationError("A product in the bill is not available any more.")
        products[p.id] = p
        it.quantity = int(it.quantity)
        if it.quantity <= 0:
            raise ValidationError(f"Quantity for '{p.name}' must be at least 1.")
        if D(it.unit_price) < 0:
            raise ValidationError(f"Selling price for '{p.name}' cannot be negative.")
        if D(it.discount) < 0 or q2(it.discount) > q2(D(it.unit_price) * it.quantity):
            raise ValidationError(f"Discount for '{p.name}' cannot be more than the item amount.")
        if p.track_imei:
            if it.quantity != 1 or not it.imei_record_id:
                raise ValidationError(f"Select the IMEI / serial number for '{p.name}' (one line per phone).")
            rec = s.get(ImeiRecord, it.imei_record_id)
            if rec is None or rec.product_id != p.id or rec.status != "IN_STOCK":
                raise ValidationError(f"IMEI selected for '{p.name}' is not in stock any more.")
            if rec.id in imei_recs:
                raise ValidationError(f"IMEI {rec.imei1} is added twice in this bill.")
            imei_recs[rec.id] = rec
        needed[p.id] += it.quantity
    for pid, qty in needed.items():
        p = products[pid]
        if p.quantity < qty:
            from .errors import InsufficientStockError
            raise InsufficientStockError(p.name, p.quantity, qty)

    flags = tax_flags()
    calc = compute_bill([LineIn(it.quantity, D(it.unit_price), D(it.discount), products[it.product_id].gst_rate)
                         for it in items], bill_discount, **flags)
    if q2(bill_discount) < 0 or q2(bill_discount) > calc.gross - calc.item_discount:
        raise ValidationError("Bill discount cannot be more than the bill amount.")
    _check_discount_limit(calc)
    if pay_full_mode:
        payments = {pay_full_mode: calc.grand_total}
    pay = {m: q2(v) for m, v in (payments or {}).items() if q2(v) > 0}
    for m in pay:
        if m not in PAYMENT_MODES and m != "EXCHANGE":
            raise ValidationError(f"Unknown payment mode {m}.")
    paid = sum(pay.values(), Decimal("0.00"))
    if paid > calc.grand_total:
        raise ValidationError(f"Payment (₹{paid:,.2f}) is more than the bill total (₹{calc.grand_total:,.2f}). "
                              "Enter only the bill amount; give the change back separately.")
    balance = calc.grand_total - paid
    if balance > 0 and cust.is_walk_in:
        raise ValidationError("This is a credit / part-payment bill. Please select or add the customer "
                              "(name and phone) so the pending balance can be tracked.")

    prefix = settings.get("invoice_prefix") or "SDJ"
    user = current_user()
    sale = Sale(invoice_no=invoice_no or next_invoice_no(s, prefix, bdate), client_ref=client_ref, sale_type=sale_type,
                customer_id=cust.id, customer_name=cust.name, customer_phone=cust.phone, business_date=bdate,
                gst_enabled=flags["gst_enabled"], price_includes_tax=flags["inclusive"],
                gross_amount=calc.gross, item_discount=calc.item_discount, bill_discount=calc.bill_discount,
                gross_ex_tax=calc.gross_ex_tax, discount_ex_tax=calc.discount_ex_tax, taxable_amount=calc.taxable,
                gst_amount=calc.gst, round_off=calc.round_off, grand_total=calc.grand_total, paid_amount=paid,
                initial_due=balance, status="COMPLETED", notes=(notes or "").strip(), created_by=user.id)
    s.add(sale)
    s.flush()
    cogs = Decimal("0.00")
    for it, lo in zip(items, calc.lines):
        p = products[it.product_id]
        unit_cost = D(p.avg_cost)
        cost_total = q2(unit_cost * it.quantity)
        rec = imei_recs.get(it.imei_record_id) if it.imei_record_id else None
        imei_text = (rec.imei1 + (f" / {rec.imei2}" if rec.imei2 else "")) if rec else (it.imei_text or "").strip()
        expiry = add_months(bdate, p.warranty_months) if p.warranty_months else None
        si = SaleItem(sale_id=sale.id, product_id=p.id, product_name=p.name, category_group=p.category.group,
                      imei_record_id=rec.id if rec else None, imei_text=imei_text, quantity=it.quantity,
                      unit_price=q2(it.unit_price), item_discount=lo.item_discount, bill_discount_share=lo.bill_share,
                      gross_amount=lo.gross, gross_ex_tax=lo.gross_ex_tax, discount_ex_tax=lo.discount_ex_tax,
                      taxable_value=lo.taxable, gst_rate=p.gst_rate if flags["gst_enabled"] else 0,
                      gst_amount=lo.gst, line_total=lo.line_total, unit_cost=unit_cost, cost_total=cost_total,
                      warranty_months=p.warranty_months, warranty_expiry=expiry)
        s.add(si)
        s.flush()
        apply_movement(s, p, -it.quantity, "SALE", business_date=bdate, ref_type="SALE", ref_id=sale.id,
                       ref_line_id=si.id, reference=sale.invoice_no)
        if rec is not None:
            rec.status, rec.sale_id, rec.sale_item_id = "SOLD", sale.id, si.id
            rec.customer_id, rec.sale_date, rec.warranty_expiry = cust.id, bdate, expiry
        cogs += cost_total
    sale.cogs_total = cogs
    for mode, amt in pay.items():
        s.add(Payment(client_ref=f"{client_ref}:{mode}" if client_ref else None, business_date=bdate,
                      direction="IN", mode=mode, amount=amt,
                      kind="EXCHANGE_CREDIT" if mode == "EXCHANGE" else "SALE_RECEIPT",
                      customer_id=cust.id, ref_type="SALE", ref_id=sale.id, reference=sale.invoice_no,
                      user_id=user.id))
    s.flush()
    audit(s, "SALE_CREATED", "sale", sale.id, {"invoice_no": sale.invoice_no, "type": sale_type,
                                              "total": sale.grand_total, "paid": paid, "items": len(items)})
    return sale


def _check_discount_limit(calc) -> None:
    """Staff may give discounts up to the owner's limit; above it an owner / manager has to approve."""
    limit = D(settings.get("staff_discount_limit") or 0)
    if limit <= 0 or calc.gross <= 0 or current_user().can("discount_override"):
        return
    pct = (calc.item_discount + calc.bill_discount) * 100 / calc.gross
    if pct > limit:
        raise PermissionDenied(f"The discount on this bill is {pct:.1f}%, above the {limit:g}% allowed for staff. "
                               "The owner or a manager has to approve it.", perm="discount_override")


def create_sale(items: list[CartItem], *, customer_id: int | None = None, bill_discount=0,
                payments: dict | None = None, notes: str = "", client_ref: str | None = None,
                sale_type: str = "BILL", pay_full_mode: str | None = None) -> dict:
    require("quick_sale" if sale_type == "QUICK" else "billing")
    try:
        with session_scope() as s:
            sale = _create_sale(s, items, customer_id=customer_id, bill_discount=bill_discount, payments=payments,
                                pay_full_mode=pay_full_mode, notes=notes, client_ref=client_ref, sale_type=sale_type)
            return dict(id=sale.id, invoice_no=sale.invoice_no, grand_total=sale.grand_total,
                        paid=sale.paid_amount, balance=sale.balance_due, cogs=sale.cogs_total)
    except IntegrityError as exc:
        raise DuplicateError("This transaction was already saved (duplicate prevented).") from exc


def quick_sale(product_id: int, quantity: int, unit_price, mode: str = "CASH", *, client_ref: str | None = None,
               imei_record_id: int | None = None) -> dict:
    if mode not in PAYMENT_MODES:
        raise ValidationError("Choose a payment mode.")
    item = CartItem(product_id=product_id, quantity=int(quantity), unit_price=q2(unit_price),
                    imei_record_id=imei_record_id)
    return create_sale([item], client_ref=client_ref, sale_type="QUICK", pay_full_mode=mode)


def _check_voidable(s, sale: Sale, what: str) -> list[Payment]:
    if sale.status != "COMPLETED":
        raise BusinessError("This bill is already cancelled.")
    if sale.sale_type == "EXCHANGE":
        raise BusinessError(f"Exchange bills cannot be {what}. Use Returns & Exchanges instead.")
    if s.scalar(select(func.count(SaleReturn.id)).where(SaleReturn.sale_id == sale.id)):
        raise BusinessError(f"Items from this bill were already returned, so it cannot be {what}.")
    assert_day_open(s, sale.business_date, f"change this bill")
    pays = s.scalars(select(Payment).where(Payment.ref_type == "SALE", Payment.ref_id == sale.id,
                                           Payment.status == "ACTIVE")).all()
    for p in pays:
        assert_day_open(s, p.business_date, "change this bill (a payment was recorded on a closed day)")
    return list(pays)


def _void_sale(s, sale: Sale, pays: list[Payment], reason: str) -> None:
    """Stock goes back, IMEI units become available again and the bill's payments are voided."""
    for si in sale.items:
        prod = s.get(Product, si.product_id)
        apply_movement(s, prod, si.quantity, "SALE_CANCEL", business_date=sale.business_date, ref_type="SALE",
                       ref_id=sale.id, ref_line_id=si.id, reference=sale.invoice_no, unit_cost=si.unit_cost,
                       notes=reason)
        if si.imei_record_id:
            rec = s.get(ImeiRecord, si.imei_record_id)
            rec.status, rec.sale_id, rec.sale_item_id = "IN_STOCK", None, None
            rec.customer_id, rec.sale_date, rec.warranty_expiry = None, None, None
    now = datetime.now().replace(microsecond=0)
    for p in pays:
        p.status, p.voided_at, p.void_reason = "VOID", now, reason
    sale.status, sale.paid_amount = "CANCELLED", Decimal("0.00")
    sale.cancelled_at, sale.cancelled_by, sale.cancel_reason = now, current_user().id, reason


def edit_problem(sale_id: int) -> str | None:
    """Why this bill can't be edited / cancelled now (None if it can)."""
    with session_scope() as s:
        sale = s.get(Sale, sale_id)
        if sale is None:
            return "Invoice not found."
        try:
            _check_voidable(s, sale, "edited")
        except BusinessError as exc:
            return str(exc)
    return None


def cancel_sale(sale_id: int, reason: str) -> None:
    """Void a bill of an open business day: stock goes back, its payments are voided."""
    require("cancel_sale")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Please enter the reason for cancelling this bill.")
    with session_scope() as s:
        sale = s.get(Sale, sale_id)
        if sale is None:
            raise NotFoundError("Invoice not found.")
        pays = _check_voidable(s, sale, "cancelled")
        _void_sale(s, sale, pays, f"Bill cancelled: {reason}")
        audit(s, "SALE_CANCELLED", "sale", sale.id, {"invoice_no": sale.invoice_no, "reason": reason,
                                                    "total": sale.grand_total})


def amend_sale(sale_id: int, items: list[CartItem], *, customer_id: int | None = None, bill_discount=0,
               payments: dict | None = None, notes: str = "", client_ref: str | None = None) -> dict:
    """Edit a bill of an open day and keep its invoice number.

    The old version is cancelled (stock back, payments voided) and renamed <number>-E1, -E2…, then the corrected
    bill is saved under the original number in the same transaction. Both versions stay in Sales History."""
    require("cancel_sale")
    try:
        with session_scope() as s:
            sale = s.get(Sale, sale_id)
            if sale is None:
                raise NotFoundError("Invoice not found.")
            require("quick_sale" if sale.sale_type == "QUICK" else "billing")
            pays = _check_voidable(s, sale, "edited")
            number, old_total, bdate, kind = sale.invoice_no, sale.grand_total, sale.business_date, sale.sale_type
            _void_sale(s, sale, pays, "Edited – replaced by the corrected bill")
            n = 1 + (s.scalar(select(func.count(Sale.id)).where(Sale.invoice_no.like(f"{number}-E%"))) or 0)
            sale.invoice_no = f"{number}-E{n}"
            s.flush()
            new = _create_sale(s, items, customer_id=customer_id, bill_discount=bill_discount, payments=payments,
                               notes=notes, client_ref=client_ref, sale_type=kind, invoice_no=number,
                               business_date=bdate)
            audit(s, "SALE_EDITED", "sale", new.id, {"invoice_no": number, "old_total": old_total,
                                                    "new_total": new.grand_total, "old_version": sale.invoice_no})
            return dict(id=new.id, invoice_no=new.invoice_no, grand_total=new.grand_total, paid=new.paid_amount,
                        balance=new.balance_due, cogs=new.cogs_total, old_version=sale.invoice_no)
    except IntegrityError as exc:
        raise DuplicateError("This change was already saved (duplicate prevented).") from exc


def collect_sale_payment(sale_id: int, amount, mode: str, notes: str = "", client_ref: str | None = None) -> int:
    require("collect_payment")
    amount = q2(amount)
    if amount <= 0:
        raise ValidationError("Enter the amount received.")
    if mode not in PAYMENT_MODES:
        raise ValidationError("Choose a payment mode.")
    with session_scope() as s:
        if client_ref and s.scalar(select(Payment.id).where(Payment.client_ref == client_ref)):
            raise DuplicateError("This payment was already recorded.")
        sale = s.get(Sale, sale_id)
        if sale is None or sale.status != "COMPLETED":
            raise BusinessError("Payments can only be collected for completed bills.")
        if amount > sale.balance_due:
            raise ValidationError(f"Amount is more than the pending balance (₹{sale.balance_due:,.2f}).")
        bdate = current_business_date(s)
        pay = Payment(client_ref=client_ref, business_date=bdate, direction="IN", mode=mode, amount=amount,
                      kind="DUE_COLLECTION", customer_id=sale.customer_id, ref_type="SALE", ref_id=sale.id,
                      reference=sale.invoice_no, notes=notes, user_id=current_user().id)
        s.add(pay)
        sale.paid_amount = sale.paid_amount + amount
        s.flush()
        audit(s, "PAYMENT_COLLECTED", "sale", sale.id, {"invoice_no": sale.invoice_no, "amount": amount, "mode": mode})
        return pay.id


# ---------------------------------------------------------------- queries
def _sale_row(sale: Sale) -> dict:
    return dict(id=sale.id, invoice_no=sale.invoice_no, business_date=sale.business_date, created_at=sale.created_at,
                customer_id=sale.customer_id, customer_name=sale.customer_name, customer_phone=sale.customer_phone,
                sale_type=sale.sale_type, grand_total=sale.grand_total, paid=sale.paid_amount - sale.refunded_amount,
                returned=sale.returned_amount, balance=sale.balance_due if sale.status == "COMPLETED" else Decimal("0.00"),
                status=sale.status, items=len(sale.items), cogs=sale.cogs_total,
                profit=(sale.taxable_amount + sale.round_off - sale.cogs_total))


def list_sales(start: date | None = None, end: date | None = None, search: str = "", status: str | None = None,
               customer_id: int | None = None, limit: int = 5000) -> list[dict]:
    with session_scope() as s:
        q = select(Sale).order_by(Sale.id.desc()).limit(limit)
        if start:
            q = q.where(Sale.business_date >= start)
        if end:
            q = q.where(Sale.business_date <= end)
        if status:
            q = q.where(Sale.status == status)
        if customer_id:
            q = q.where(Sale.customer_id == customer_id)
        if search:
            like = f"%{search.strip()}%"
            sub = select(SaleItem.sale_id).where(or_(SaleItem.imei_text.like(like), SaleItem.product_name.ilike(like)))
            q = q.where(or_(Sale.invoice_no.ilike(like), Sale.customer_name.ilike(like),
                            Sale.customer_phone.like(like), Sale.id.in_(sub)))
        return [_sale_row(x) for x in s.scalars(q)]


def get_sale(sale_id: int) -> dict:
    with session_scope() as s:
        sale = s.get(Sale, sale_id)
        if sale is None:
            raise NotFoundError("Invoice not found.")
        d = _sale_row(sale)
        d.update(gross_amount=sale.gross_amount, item_discount=sale.item_discount, bill_discount=sale.bill_discount,
                 taxable_amount=sale.taxable_amount, gst_amount=sale.gst_amount, round_off=sale.round_off,
                 gst_enabled=sale.gst_enabled, price_includes_tax=sale.price_includes_tax, notes=sale.notes,
                 cancel_reason=sale.cancel_reason, refunded=sale.refunded_amount, paid_gross=sale.paid_amount)
        cust = s.get(Customer, sale.customer_id)
        d["customer"] = dict(name=cust.name, phone=cust.phone, address=cust.address, gstin=cust.gstin,
                             is_walk_in=cust.is_walk_in)
        d["lines"] = [dict(id=i.id, product_id=i.product_id, product_name=i.product_name, imei=i.imei_text,
                           imei_record_id=i.imei_record_id,
                           quantity=i.quantity, unit_price=i.unit_price,
                           discount=i.item_discount + i.bill_discount_share, item_discount=i.item_discount,
                           taxable=i.taxable_value, gst_rate=i.gst_rate, gst=i.gst_amount, total=i.line_total,
                           unit_cost=i.unit_cost, cost=i.cost_total, returned_qty=i.returned_qty,
                           warranty_months=i.warranty_months, warranty_expiry=i.warranty_expiry,
                           group=i.category_group) for i in sale.items]
        pays = s.scalars(select(Payment).where(Payment.ref_type == "SALE", Payment.ref_id == sale.id)
                         .order_by(Payment.id)).all()
        d["payments"] = [dict(id=p.id, date=p.business_date, created_at=p.created_at, direction=p.direction,
                              mode=p.mode, mode_label=MODE_LABELS.get(p.mode, p.mode), amount=p.amount, kind=p.kind,
                              status=p.status) for p in pays]
        rets = s.scalars(select(SaleReturn).where(SaleReturn.sale_id == sale.id).order_by(SaleReturn.id)).all()
        d["returns"] = [dict(id=r.id, return_no=r.return_no, date=r.business_date, value=r.total_value,
                             refund=r.refund_amount, type=r.return_type) for r in rets]
        return d


def find_sale_id(text: str) -> int | None:
    """Find an invoice by number (full or trailing digits) or by IMEI sold on it."""
    t = (text or "").strip()
    if not t:
        return None
    with session_scope() as s:
        sid = s.scalar(select(Sale.id).where(func.upper(Sale.invoice_no) == t.upper()))
        if sid:
            return sid
        if t.isdigit():
            sid = s.scalar(select(Sale.id).where(Sale.invoice_no.like(f"%/{int(t):05d}")).order_by(Sale.id.desc()))
            if sid:
                return sid
        return s.scalar(select(SaleItem.sale_id).where(SaleItem.imei_text.like(f"%{t}%")).order_by(SaleItem.id.desc()))
