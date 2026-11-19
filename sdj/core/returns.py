"""Sales returns (full / partial), refunds, damaged goods and exchanges."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from ..db.base import D, q2, session_scope
from ..db.models import ImeiRecord, Payment, Product, ReturnItem, Sale, SaleItem, SaleReturn
from . import settings
from .audit import audit
from .auth import current_user, require
from .business_day import current_business_date
from .errors import BusinessError, DuplicateError, NotFoundError, ValidationError
from .inventory import apply_movement
from .numbering import next_credit_note_no
from .sales import PAYMENT_MODES, CartItem, _create_sale, get_sale

RETURN_REASONS = ["Defective / not working", "Customer changed mind", "Wrong product given", "Damaged in box",
                  "Exchange for another model", "Warranty replacement", "Other"]


def returnable_lines(sale_id: int) -> dict:
    d = get_sale(sale_id)
    if d["status"] != "COMPLETED":
        raise BusinessError("This bill is cancelled; nothing can be returned.")
    for ln in d["lines"]:
        ln["returnable_qty"] = ln["quantity"] - ln["returned_qty"]
        ln["unit_value"] = q2(ln["total"] / ln["quantity"]) if ln["quantity"] else Decimal("0.00")
    return d


def _line_values(si: SaleItem, qty: int) -> tuple[Decimal, Decimal]:
    remaining = si.quantity - si.returned_qty
    if qty == remaining:
        value = si.line_total - si.returned_value
        taxable = si.taxable_value - si.returned_taxable
    else:
        value = q2(D(si.line_total) * qty / si.quantity)
        taxable = q2(D(si.taxable_value) * qty / si.quantity)
    return value, taxable


def preview_return(sale_id: int, items: list[dict]) -> dict:
    """Value of the selected items and how much money can be refunded."""
    with session_scope() as s:
        sale = s.get(Sale, sale_id)
        value = Decimal("0.00")
        all_back = True
        chosen = {int(i["sale_item_id"]): int(i["quantity"]) for i in items if int(i.get("quantity") or 0) > 0}
        for si in sale.items:
            q = chosen.get(si.id, 0)
            if q:
                v, _ = _line_values(si, q)
                value += v
            if si.returned_qty + q < si.quantity:
                all_back = False
        if all_back and chosen:
            value += sale.round_off
        new_total = sale.grand_total - sale.returned_amount - value
        net_paid = sale.paid_amount - sale.refunded_amount
        refundable = max(net_paid - new_total, Decimal("0.00"))
        due_reduction = value - refundable
        return dict(value=value, refundable=refundable, due_reduction=max(due_reduction, Decimal("0.00")),
                    net_paid=net_paid, current_balance=sale.balance_due)


def create_return(sale_id: int, items: list[dict], *, reason: str, refund_mode: str = "CASH",
                  refund_amount=None, exchange_items: list[CartItem] | None = None,
                  exchange_payments: dict | None = None, notes: str = "", client_ref: str | None = None) -> dict:
    """items: [{sale_item_id, quantity, condition: RESALABLE|DAMAGED}]"""
    require("returns")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Please choose or enter a return reason.")
    chosen = [dict(sale_item_id=int(i["sale_item_id"]), quantity=int(i.get("quantity") or 0),
                   condition=(i.get("condition") or "RESALABLE").upper()) for i in items]
    chosen = [c for c in chosen if c["quantity"] > 0]
    if not chosen:
        raise ValidationError("Select at least one item and quantity to return.")
    try:
        with session_scope() as s:
            if client_ref and s.scalar(select(SaleReturn.id).where(SaleReturn.client_ref == client_ref)):
                raise DuplicateError("This return was already saved (duplicate prevented).")
            sale = s.get(Sale, sale_id)
            if sale is None:
                raise NotFoundError("Invoice not found.")
            if sale.status != "COMPLETED":
                raise BusinessError("This bill is cancelled; nothing can be returned.")
            bdate = current_business_date(s)
            user = current_user()
            ret = SaleReturn(return_no=next_credit_note_no(s, settings.get("credit_note_prefix") or "CN", bdate),
                             client_ref=client_ref, sale_id=sale.id, customer_id=sale.customer_id, business_date=bdate,
                             return_type="EXCHANGE" if exchange_items else "PARTIAL", reason=reason,
                             notes=(notes or "").strip(), created_by=user.id)
            s.add(ret)
            s.flush()
            lines = {si.id: si for si in sale.items}
            total = taxable_total = cost_back = damaged = Decimal("0.00")
            for c in chosen:
                si = lines.get(c["sale_item_id"])
                if si is None:
                    raise ValidationError("Selected item does not belong to this invoice.")
                remaining = si.quantity - si.returned_qty
                if c["quantity"] > remaining:
                    raise ValidationError(f"Only {remaining} unit(s) of '{si.product_name}' can still be returned.")
                if c["condition"] not in ("RESALABLE", "DAMAGED"):
                    raise ValidationError("Condition must be Resalable or Damaged.")
                value, taxable = _line_values(si, c["quantity"])
                cost = q2(D(si.unit_cost) * c["quantity"])
                restock = c["condition"] == "RESALABLE"
                ri = ReturnItem(return_id=ret.id, sale_item_id=si.id, product_id=si.product_id,
                                product_name=si.product_name, category_group=si.category_group,
                                quantity=c["quantity"], condition=c["condition"], total_value=value,
                                taxable_value=taxable, gst_value=value - taxable, unit_cost=si.unit_cost,
                                cost_total=cost, imei_text=si.imei_text, restocked=restock)
                s.add(ri)
                s.flush()
                if restock:
                    apply_movement(s, s.get(Product, si.product_id), c["quantity"], "SALE_RETURN", business_date=bdate,
                                   ref_type="RETURN", ref_id=ret.id, ref_line_id=ri.id, reference=ret.return_no,
                                   unit_cost=si.unit_cost, notes=f"Return of {sale.invoice_no}: {reason}")
                    cost_back += cost
                else:
                    damaged += cost
                if si.imei_record_id:
                    rec = s.get(ImeiRecord, si.imei_record_id)
                    rec.status = "IN_STOCK" if restock else "DAMAGED"
                    rec.sale_id = rec.sale_item_id = rec.customer_id = None
                    rec.sale_date = rec.warranty_expiry = None
                    rec.notes = (rec.notes + "\n" if rec.notes else "") + f"Returned on {ret.return_no} ({reason})"
                si.returned_qty += c["quantity"]
                si.returned_value = si.returned_value + value
                si.returned_taxable = si.returned_taxable + taxable
                total += value
                taxable_total += taxable
            if all(si.returned_qty >= si.quantity for si in sale.items):
                ret.round_off_value = sale.round_off
                total += sale.round_off
                if not exchange_items:
                    ret.return_type = "FULL"
            ret.total_value, ret.taxable_value = total, taxable_total
            ret.gst_value = total - ret.round_off_value - taxable_total
            ret.cost_reversed, ret.damaged_cost = cost_back, damaged
            sale.returned_amount = sale.returned_amount + total

            overpaid = (sale.paid_amount - sale.refunded_amount) - (sale.grand_total - sale.returned_amount)
            overpaid = max(overpaid, Decimal("0.00"))
            exchange_sale = None
            if exchange_items:
                exchange_sale = _apply_exchange(s, sale, ret, overpaid, exchange_items, exchange_payments, client_ref)
                overpaid -= ret.exchange_credit
            refund = overpaid if refund_amount is None else q2(refund_amount)
            if refund < 0 or refund > overpaid:
                raise ValidationError(f"Refund cannot be more than ₹{overpaid:,.2f} (the amount the customer has paid "
                                      "beyond the remaining bill value).")
            if refund > 0:
                if refund_mode not in PAYMENT_MODES:
                    raise ValidationError("Choose how the refund is given.")
                s.add(Payment(client_ref=f"{client_ref}:refund" if client_ref else None, business_date=bdate,
                              direction="OUT", mode=refund_mode, amount=refund, kind="REFUND",
                              customer_id=sale.customer_id, ref_type="SALE", ref_id=sale.id,
                              reference=ret.return_no, notes=f"Refund against {sale.invoice_no}", user_id=user.id))
                sale.refunded_amount = sale.refunded_amount + refund
            ret.refund_amount, ret.refund_mode = refund, refund_mode if refund > 0 else ""
            s.flush()
            audit(s, "RETURN_CREATED", "return", ret.id, {"return_no": ret.return_no, "invoice_no": sale.invoice_no,
                                                         "value": total, "refund": refund,
                                                         "exchange_invoice": exchange_sale.invoice_no if exchange_sale else ""})
            return dict(id=ret.id, return_no=ret.return_no, value=total, refund=refund,
                        exchange_sale_id=exchange_sale.id if exchange_sale else None,
                        exchange_invoice_no=exchange_sale.invoice_no if exchange_sale else None,
                        exchange_credit=ret.exchange_credit)
    except IntegrityError as exc:
        raise DuplicateError("This return was already saved (duplicate prevented).") from exc


def _apply_exchange(s, sale: Sale, ret: SaleReturn, overpaid: Decimal, items: list[CartItem],
                    payments: dict | None, client_ref: str | None) -> Sale:
    from .calc import LineIn, compute_bill
    from .sales import tax_flags
    lines = []
    for it in items:
        p = s.get(Product, it.product_id)
        if p is None:
            raise ValidationError("Exchange product not found.")
        lines.append(LineIn(int(it.quantity), D(it.unit_price), D(it.discount), p.gst_rate))
    new_total = compute_bill(lines, 0, **tax_flags()).grand_total
    credit = min(overpaid, new_total)
    pays = {k: q2(v) for k, v in (payments or {}).items() if q2(v) > 0}
    if credit > 0:
        pays["EXCHANGE"] = credit
    new_sale = _create_sale(s, items, customer_id=sale.customer_id, payments=pays,
                            notes=f"Exchange against {sale.invoice_no} ({ret.return_no})",
                            client_ref=f"{client_ref}:exchange" if client_ref else None, sale_type="EXCHANGE")
    if credit > 0:
        s.add(Payment(client_ref=f"{client_ref}:xcredit" if client_ref else None, business_date=ret.business_date,
                      direction="OUT", mode="EXCHANGE", amount=credit, kind="EXCHANGE_CREDIT",
                      customer_id=sale.customer_id, ref_type="SALE", ref_id=sale.id, reference=ret.return_no,
                      notes=f"Credit moved to {new_sale.invoice_no}", user_id=current_user().id))
        sale.refunded_amount = sale.refunded_amount + credit
    ret.exchange_sale_id, ret.exchange_credit = new_sale.id, credit
    return new_sale


def list_returns(start: date | None = None, end: date | None = None, search: str = "") -> list[dict]:
    with session_scope() as s:
        q = select(SaleReturn, Sale.invoice_no, Sale.customer_name).join(Sale, SaleReturn.sale_id == Sale.id) \
            .order_by(SaleReturn.id.desc())
        if start:
            q = q.where(SaleReturn.business_date >= start)
        if end:
            q = q.where(SaleReturn.business_date <= end)
        if search:
            like = f"%{search}%"
            q = q.where(or_(SaleReturn.return_no.ilike(like), Sale.invoice_no.ilike(like), Sale.customer_name.ilike(like)))
        out = []
        for r, inv, cname in s.execute(q):
            ex_no = s.scalar(select(Sale.invoice_no).where(Sale.id == r.exchange_sale_id)) if r.exchange_sale_id else ""
            out.append(dict(id=r.id, return_no=r.return_no, business_date=r.business_date, invoice_no=inv,
                            customer=cname, type=r.return_type, reason=r.reason, value=r.total_value,
                            refund=r.refund_amount, refund_mode=r.refund_mode, exchange_invoice=ex_no or "",
                            items=", ".join(f"{i.product_name} x{i.quantity}" for i in r.items)))
        return out


def damaged_items(start: date | None = None, end: date | None = None) -> list[dict]:
    with session_scope() as s:
        q = select(ReturnItem, SaleReturn.return_no, SaleReturn.business_date, SaleReturn.reason) \
            .join(SaleReturn).where(ReturnItem.condition == "DAMAGED").order_by(ReturnItem.id.desc())
        if start:
            q = q.where(SaleReturn.business_date >= start)
        if end:
            q = q.where(SaleReturn.business_date <= end)
        return [dict(return_no=no, business_date=d, product=i.product_name, quantity=i.quantity,
                     imei=i.imei_text, cost=i.cost_total, value=i.total_value, reason=reason)
                for i, no, d, reason in s.execute(q)]
