"""IMEI / serial tracking, IMEI history and warranty claims."""
from __future__ import annotations

from datetime import date

from sqlalchemy import or_, select

from ..db.base import session_scope
from ..db.models import (ImeiRecord, Product, Purchase, PurchaseItem, ReturnItem, Sale, SaleItem, SaleReturn,
                         ServiceJob, WarrantyClaim)
from .audit import audit
from .auth import current_user, require
from .business_day import current_business_date
from .errors import NotFoundError, ValidationError
from .numbering import next_simple

IMEI_STATUS_LABELS = {"IN_STOCK": "In stock", "SOLD": "Sold", "DAMAGED": "Damaged", "REMOVED": "Removed"}
CLAIM_STATUSES = ["OPEN", "SENT_TO_SERVICE_CENTER", "REPAIRED", "REPLACED", "REJECTED", "CLOSED"]
CLAIM_LABELS = {"OPEN": "Open", "SENT_TO_SERVICE_CENTER": "Sent to service centre", "REPAIRED": "Repaired",
                "REPLACED": "Replaced", "REJECTED": "Rejected", "CLOSED": "Closed"}


def warranty_text(expiry: date | None) -> str:
    if not expiry:
        return "No warranty"
    if expiry >= date.today():
        return f"In warranty (till {expiry.strftime('%d %b %Y')})"
    return f"Expired on {expiry.strftime('%d %b %Y')}"


def search_imei(text: str = "", status: str | None = None, limit: int = 2000) -> list[dict]:
    with session_scope() as s:
        q = select(ImeiRecord, Product.name).join(Product).order_by(ImeiRecord.id.desc()).limit(limit)
        if text:
            like = f"%{text.strip()}%"
            q = q.where(or_(ImeiRecord.imei1.like(like), ImeiRecord.imei2.like(like), ImeiRecord.serial_no.like(like),
                            Product.name.ilike(like)))
        if status:
            q = q.where(ImeiRecord.status == status)
        out = []
        for r, pname in s.execute(q):
            inv = cust = ""
            if r.sale_id:
                sale = s.get(Sale, r.sale_id)
                inv, cust = sale.invoice_no, f"{sale.customer_name} {sale.customer_phone}".strip()
            out.append(dict(id=r.id, imei1=r.imei1, imei2=r.imei2, serial_no=r.serial_no, product=pname,
                            product_id=r.product_id, status=r.status, status_label=IMEI_STATUS_LABELS.get(r.status, r.status),
                            purchase_date=r.purchase_date, purchase_cost=r.purchase_cost, sale_date=r.sale_date,
                            invoice_no=inv, customer=cust, warranty_expiry=r.warranty_expiry,
                            warranty=warranty_text(r.warranty_expiry) if r.status == "SOLD" else ""))
        return out


def imei_history(value: str) -> list[dict]:
    """Complete timeline for an IMEI/serial: purchase, sales, returns, repairs, claims."""
    v = (value or "").strip()
    if not v:
        raise ValidationError("Enter an IMEI or serial number.")
    events = []
    with session_scope() as s:
        recs = s.scalars(select(ImeiRecord).where(or_(ImeiRecord.imei1 == v, ImeiRecord.imei2 == v,
                                                      ImeiRecord.serial_no == v))).all()
        for r in recs:
            src = {"OPENING": "Added as opening stock", "ADJUSTMENT": "Added by stock adjustment"}.get(r.source, "Purchased")
            ref = ""
            if r.purchase_item_id:
                pi = s.get(PurchaseItem, r.purchase_item_id)
                pur = s.get(Purchase, pi.purchase_id)
                ref = f"{pur.purchase_no} ({pur.supplier.name})"
            events.append(dict(date=r.purchase_date or r.created_at.date(), event=src, product=r.product.name,
                               reference=ref, details=f"Cost ₹{r.purchase_cost:,.2f}; status now: "
                                                      f"{IMEI_STATUS_LABELS.get(r.status, r.status)}"))
        like = f"%{v}%"
        for si, sale in s.execute(select(SaleItem, Sale).join(Sale).where(SaleItem.imei_text.like(like))):
            events.append(dict(date=sale.business_date, event="Sold" if sale.status == "COMPLETED" else "Sale cancelled",
                               product=si.product_name, reference=sale.invoice_no,
                               details=f"{sale.customer_name} {sale.customer_phone} • ₹{si.line_total:,.2f} • "
                                       f"{warranty_text(si.warranty_expiry)}"))
        for ri, ret in s.execute(select(ReturnItem, SaleReturn).join(SaleReturn).where(ReturnItem.imei_text.like(like))):
            events.append(dict(date=ret.business_date, event=f"Returned ({ri.condition.lower()})", product=ri.product_name,
                               reference=ret.return_no, details=ret.reason))
        for j in s.scalars(select(ServiceJob).where(ServiceJob.imei.like(like))):
            events.append(dict(date=j.received_date, event="Repair job", product=f"{j.brand} {j.model}".strip(),
                               reference=j.job_no, details=f"{j.problem} • {j.status.replace('_', ' ').title()}"))
        for c in s.scalars(select(WarrantyClaim).where(WarrantyClaim.imei_text.like(like))):
            events.append(dict(date=c.claim_date, event="Warranty claim", product=c.product_name, reference=c.claim_no,
                               details=f"{c.issue} • {CLAIM_LABELS.get(c.status, c.status)}"))
    events.sort(key=lambda e: e["date"] or date.min)
    return events


def create_claim(imei_text: str, issue: str, notes: str = "") -> dict:
    require("services")
    imei_text = (imei_text or "").strip()
    if not imei_text or not (issue or "").strip():
        raise ValidationError("IMEI/serial and the issue are required.")
    with session_scope() as s:
        rec = s.scalar(select(ImeiRecord).where(or_(ImeiRecord.imei1 == imei_text, ImeiRecord.imei2 == imei_text,
                                                    ImeiRecord.serial_no == imei_text)).order_by(ImeiRecord.id.desc()))
        sale = s.get(Sale, rec.sale_id) if rec and rec.sale_id else None
        claim = WarrantyClaim(claim_no=next_simple(s, "claim_no", "WC"), imei_record_id=rec.id if rec else None,
                              imei_text=imei_text, product_id=rec.product_id if rec else None,
                              product_name=rec.product.name if rec else "", sale_id=sale.id if sale else None,
                              customer_id=sale.customer_id if sale else None,
                              customer_name=sale.customer_name if sale else "",
                              customer_phone=sale.customer_phone if sale else "", claim_date=current_business_date(s),
                              issue=issue.strip(), status="OPEN", notes=(notes or "").strip(),
                              created_by=current_user().id)
        s.add(claim)
        s.flush()
        audit(s, "WARRANTY_CLAIM", "warranty_claim", claim.id, {"claim_no": claim.claim_no, "imei": imei_text})
        return dict(id=claim.id, claim_no=claim.claim_no,
                    warranty=warranty_text(rec.warranty_expiry) if rec else "Not sold by this shop / unknown")


def update_claim(claim_id: int, status: str, resolution: str = "", notes: str | None = None) -> None:
    require("services")
    if status not in CLAIM_STATUSES:
        raise ValidationError("Invalid claim status.")
    with session_scope() as s:
        c = s.get(WarrantyClaim, claim_id)
        if c is None:
            raise NotFoundError("Claim not found.")
        old = c.status
        c.status, c.resolution = status, (resolution or "").strip()
        if notes is not None:
            c.notes = notes.strip()
        audit(s, "WARRANTY_CLAIM_UPDATED", "warranty_claim", c.id, {"from": old, "to": status})


def list_claims(search: str = "", status: str | None = None) -> list[dict]:
    with session_scope() as s:
        q = select(WarrantyClaim).order_by(WarrantyClaim.id.desc())
        if search:
            like = f"%{search}%"
            q = q.where(or_(WarrantyClaim.imei_text.like(like), WarrantyClaim.claim_no.ilike(like),
                            WarrantyClaim.customer_name.ilike(like), WarrantyClaim.customer_phone.like(like)))
        if status:
            q = q.where(WarrantyClaim.status == status)
        return [dict(id=c.id, claim_no=c.claim_no, claim_date=c.claim_date, imei=c.imei_text, product=c.product_name,
                     customer=f"{c.customer_name} {c.customer_phone}".strip(), issue=c.issue, status=c.status,
                     status_label=CLAIM_LABELS.get(c.status, c.status), resolution=c.resolution, notes=c.notes)
                for c in s.scalars(q)]
