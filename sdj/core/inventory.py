"""Products, stock movements (single gateway for every stock change), IMEI units."""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from sqlalchemy import func, or_, select

from ..db.base import D, q2, q4, session_scope
from ..db.models import (Category, ImeiRecord, Product, PurchaseItem, SaleItem, ServiceItem,
                         StockMovement, Supplier)
from . import settings
from .audit import audit
from .auth import current_user, require
from .business_day import current_business_date
from .calc import weighted_average
from .errors import InsufficientStockError, NotFoundError, ValidationError
from .numbering import next_product_code

GROUP_LABELS = {"MOBILE": "Mobile Phones", "ACCESSORY": "Accessories", "SPARE": "Spare Parts"}

MOVEMENT_LABELS = {
    "OPENING": "Opening stock", "PURCHASE": "Purchase", "PURCHASE_CANCEL": "Purchase cancelled",
    "SALE": "Sale", "SALE_CANCEL": "Sale cancelled", "SALE_RETURN": "Sales return",
    "SERVICE_USE": "Used in service", "SERVICE_RETURN": "Returned from service",
    "ADJUST_IN": "Adjustment (+)", "ADJUST_OUT": "Adjustment (−)", "DAMAGED": "Damaged / write-off",
}
INBOUND_TYPES = {"OPENING", "PURCHASE", "SALE_CANCEL", "SALE_RETURN", "SERVICE_RETURN", "ADJUST_IN"}


# ---------------------------------------------------------------- categories
def list_categories(group: str | None = None, active_only: bool = True) -> list[dict]:
    with session_scope() as s:
        q = select(Category).order_by(Category.sort_order, Category.name)
        if group:
            q = q.where(Category.group == group)
        if active_only:
            q = q.where(Category.is_active.is_(True))
        return [dict(id=c.id, name=c.name, group=c.group, group_label=GROUP_LABELS[c.group],
                     requires_imei=c.requires_imei) for c in s.scalars(q)]


def add_category(name: str, group: str, requires_imei: bool = False) -> int:
    require("products_edit")
    name = (name or "").strip()
    if not name:
        raise ValidationError("Category name is required.")
    if group not in GROUP_LABELS:
        raise ValidationError("Choose a valid category group.")
    with session_scope() as s:
        if s.scalar(select(Category).where(func.lower(Category.name) == name.lower())):
            raise ValidationError("A category with this name already exists.")
        c = Category(name=name, group=group, requires_imei=requires_imei, sort_order=999)
        s.add(c)
        s.flush()
        audit(s, "CATEGORY_CREATED", "category", c.id, {"name": name, "group": group})
        return c.id


# ---------------------------------------------------------------- IMEI helpers
def luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def parse_imei_lines(text: str) -> list[dict]:
    """Each line: IMEI1[, IMEI2][, SERIAL]. Blank lines ignored."""
    units = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = [p.strip() for p in re.split(r"[,;\t|]", line)]
        units.append({"imei1": parts[0], "imei2": parts[1] if len(parts) > 1 else "",
                      "serial_no": parts[2] if len(parts) > 2 else ""})
    return units


def validate_imei(value: str, label: str = "IMEI") -> str:
    v = (value or "").strip().replace(" ", "")
    if not v:
        raise ValidationError(f"{label} is required.")
    if not re.fullmatch(r"[A-Za-z0-9\-/]{6,24}", v):
        raise ValidationError(f"{label} '{value}' is not valid (use 6–24 letters/digits).")
    if v.isdigit() and len(v) == 15 and not luhn_ok(v):
        raise ValidationError(f"{label} {v} looks wrong – its check digit does not match. Please re-check it.")
    return v


def _validate_units(session, units: list[dict], expected: int) -> list[dict]:
    if len(units) != expected:
        raise ValidationError(f"Please enter exactly {expected} IMEI/serial number(s); {len(units)} entered.")
    clean, seen = [], set()
    for u in units:
        imei1 = validate_imei(u.get("imei1", ""))
        imei2 = validate_imei(u["imei2"], "IMEI 2") if u.get("imei2") else ""
        for v in filter(None, (imei1, imei2)):
            if v in seen:
                raise ValidationError(f"IMEI {v} is entered more than once.")
            seen.add(v)
            dup = session.scalar(select(ImeiRecord).where(
                ImeiRecord.status == "IN_STOCK", or_(ImeiRecord.imei1 == v, ImeiRecord.imei2 == v)))
            if dup is not None:
                raise ValidationError(f"IMEI {v} is already in stock (product: {dup.product.name}).")
        clean.append({"imei1": imei1, "imei2": imei2, "serial_no": (u.get("serial_no") or "").strip()})
    return clean


def add_imei_units(session, product: Product, units: list[dict], *, source: str, cost,
                   purchase_date: date | None, purchase_item_id: int | None = None) -> list[ImeiRecord]:
    clean = _validate_units(session, units, len(units))
    recs = []
    for u in clean:
        r = ImeiRecord(product_id=product.id, imei1=u["imei1"], imei2=u["imei2"], serial_no=u["serial_no"],
                       status="IN_STOCK", source=source, purchase_item_id=purchase_item_id,
                       purchase_date=purchase_date, purchase_cost=q2(cost))
        session.add(r)
        recs.append(r)
    session.flush()
    return recs


def available_imeis(product_id: int) -> list[dict]:
    with session_scope() as s:
        rows = s.scalars(select(ImeiRecord).where(ImeiRecord.product_id == product_id,
                                                  ImeiRecord.status == "IN_STOCK").order_by(ImeiRecord.id))
        return [dict(id=r.id, imei1=r.imei1, imei2=r.imei2, serial_no=r.serial_no,
                     label=r.imei1 + (f" / {r.imei2}" if r.imei2 else "") + (f"  (S/N {r.serial_no})" if r.serial_no else ""))
                for r in rows]


def find_imei_in_stock(text: str) -> dict | None:
    v = (text or "").strip()
    if not v:
        return None
    with session_scope() as s:
        r = s.scalar(select(ImeiRecord).where(ImeiRecord.status == "IN_STOCK",
                                              or_(ImeiRecord.imei1 == v, ImeiRecord.imei2 == v,
                                                  ImeiRecord.serial_no == v)))
        if r is None:
            return None
        return dict(id=r.id, product_id=r.product_id, imei1=r.imei1, imei2=r.imei2, serial_no=r.serial_no)


# ---------------------------------------------------------------- the stock gateway
def apply_movement(session, product: Product, qty_change: int, mtype: str, *, business_date: date,
                   ref_type: str = "", ref_id: int | None = None, ref_line_id: int | None = None,
                   reference: str = "", unit_cost=None, notes: str = "", allow_negative: bool = False) -> StockMovement:
    """The ONLY place where product quantity changes. Keeps weighted-average cost."""
    if qty_change == 0:
        raise ValidationError("Quantity change cannot be zero.")
    old_qty = product.quantity or 0
    new_qty = old_qty + qty_change
    if new_qty < 0 and not allow_negative:
        raise InsufficientStockError(product.name, old_qty, -qty_change)
    avg = D(product.avg_cost or 0)
    if qty_change > 0:
        cost = D(unit_cost) if unit_cost is not None else avg
        new_avg = weighted_average(old_qty, avg, qty_change, cost)
    else:
        cost = avg
        new_avg = avg
        if mtype == "PURCHASE_CANCEL" and unit_cost is not None:
            cost = D(unit_cost)
            if new_qty > 0:
                remaining_value = Decimal(old_qty) * avg - Decimal(-qty_change) * cost
                new_avg = max(remaining_value / Decimal(new_qty), Decimal("0"))
    product.avg_cost = q4(new_avg)
    product.quantity = new_qty
    mv = StockMovement(product_id=product.id, business_date=business_date, movement_type=mtype,
                       qty_change=qty_change, qty_after=new_qty, unit_cost=q4(cost),
                       avg_cost_after=product.avg_cost, ref_type=ref_type, ref_id=ref_id,
                       ref_line_id=ref_line_id, reference=reference, notes=notes,
                       user_id=current_user().id)
    session.add(mv)
    session.flush()  # surfaces the unique constraint immediately (no double deduction)
    return mv


# ---------------------------------------------------------------- products
PRODUCT_FIELDS = ("name", "brand", "category_id", "model_number", "barcode", "purchase_price", "selling_price",
                  "min_stock", "supplier_id", "purchase_date", "warranty_months", "description", "gst_rate",
                  "hsn_code", "track_imei")


def _clean_product(session, data: dict, product_id: int | None) -> dict:
    d = {k: data.get(k) for k in PRODUCT_FIELDS if k in data}
    d["name"] = (d.get("name") or "").strip()
    if not d["name"]:
        raise ValidationError("Product name is required.")
    cat = session.get(Category, d.get("category_id")) if d.get("category_id") else None
    if cat is None:
        raise ValidationError("Please choose a category.")
    for k in ("brand", "model_number", "description", "hsn_code"):
        d[k] = (d.get(k) or "").strip()
    bc = (d.get("barcode") or "").strip()
    d["barcode"] = bc or None
    if bc:
        q = select(Product).where(Product.barcode == bc)
        if product_id:
            q = q.where(Product.id != product_id)
        other = session.scalar(q)
        if other is not None:
            raise ValidationError(f"Barcode {bc} is already used by '{other.name}'.")
    if d.get("gst_rate") in (None, ""):
        d["gst_rate"] = settings.get({"MOBILE": "default_gst_mobile", "ACCESSORY": "default_gst_accessory",
                                      "SPARE": "default_gst_spare"}[cat.group]) or 0
    for k in ("purchase_price", "selling_price", "gst_rate"):
        v = q2(d.get(k) or 0)
        if v < 0:
            raise ValidationError("Prices and GST rate cannot be negative.")
        d[k] = v
    if d["gst_rate"] > 28:
        raise ValidationError("GST rate looks too high (maximum 28%).")
    for k in ("min_stock", "warranty_months"):
        v = int(d.get(k) or 0)
        if v < 0:
            raise ValidationError("Minimum stock and warranty cannot be negative.")
        d[k] = v
    d["track_imei"] = bool(d.get("track_imei", cat.requires_imei))
    if d.get("supplier_id") in ("", 0):
        d["supplier_id"] = None
    return d


def create_product(data: dict, opening_qty: int = 0, opening_cost=None, imei_units: list[dict] | None = None) -> int:
    require("products_edit")
    opening_qty = int(opening_qty or 0)
    if opening_qty < 0:
        raise ValidationError("Opening stock cannot be negative.")
    with session_scope() as s:
        d = _clean_product(s, data, None)
        p = Product(code=next_product_code(s), quantity=0, avg_cost=0, **d)
        s.add(p)
        s.flush()
        if opening_qty > 0:
            cost = q2(opening_cost if opening_cost is not None else d["purchase_price"])
            bdate = current_business_date(s)
            if p.track_imei:
                add_imei_units(s, p, _validate_units(s, imei_units or [], opening_qty), source="OPENING",
                               cost=cost, purchase_date=d.get("purchase_date") or bdate)
            apply_movement(s, p, opening_qty, "OPENING", business_date=bdate, ref_type="OPENING",
                           ref_id=p.id, ref_line_id=p.id, reference=p.code, unit_cost=cost,
                           notes="Opening stock")
        audit(s, "PRODUCT_CREATED", "product", p.id, {"code": p.code, "name": p.name, "opening_qty": opening_qty})
        return p.id


def update_product(product_id: int, data: dict) -> None:
    require("products_edit")
    with session_scope() as s:
        p = s.get(Product, product_id)
        if p is None:
            raise NotFoundError("Product not found.")
        d = _clean_product(s, {**{k: getattr(p, k) for k in PRODUCT_FIELDS}, **data}, product_id)
        if d["track_imei"] != p.track_imei and p.quantity != 0:
            raise ValidationError("IMEI tracking can only be switched on/off when stock is zero.")
        before = {k: getattr(p, k) for k in d}
        for k, v in d.items():
            setattr(p, k, v)
        changes = {k: {"from": before[k], "to": v} for k, v in d.items() if before[k] != v}
        if changes:
            audit(s, "PRODUCT_UPDATED", "product", p.id, {"code": p.code, "changes": changes})


def set_product_active(product_id: int, active: bool) -> None:
    require("products_edit")
    with session_scope() as s:
        p = s.get(Product, product_id)
        if p is None:
            raise NotFoundError("Product not found.")
        p.is_active = active
        audit(s, "PRODUCT_ACTIVATED" if active else "PRODUCT_DEACTIVATED", "product", p.id, {"code": p.code})


def delete_product(product_id: int) -> None:
    """Only products that were never used anywhere can be deleted; others are deactivated."""
    require("products_edit")
    with session_scope() as s:
        p = s.get(Product, product_id)
        if p is None:
            raise NotFoundError("Product not found.")
        used = any(s.scalar(select(func.count()).select_from(t).where(t.product_id == product_id))
                   for t in (StockMovement, SaleItem, PurchaseItem, ServiceItem))
        if used:
            raise ValidationError("This product has stock or sales history, so it cannot be deleted. "
                                  "Use 'Deactivate' instead to hide it.")
        audit(s, "PRODUCT_DELETED", "product", p.id, {"code": p.code, "name": p.name})
        s.delete(p)


def _status(p_qty: int, min_stock: int) -> str:
    if p_qty <= 0:
        return "OUT"
    if min_stock > 0 and p_qty <= min_stock:
        return "LOW"
    return "OK"


def _pdict(p: Product) -> dict:
    qty = p.quantity or 0
    return dict(id=p.id, code=p.code, name=p.name, brand=p.brand, category_id=p.category_id,
                category=p.category.name, group=p.category.group, group_label=GROUP_LABELS[p.category.group],
                model_number=p.model_number, barcode=p.barcode or "", purchase_price=p.purchase_price,
                selling_price=p.selling_price, avg_cost=p.avg_cost, quantity=qty, min_stock=p.min_stock,
                stock_value=q2(D(qty) * D(p.avg_cost)) if qty > 0 else Decimal("0.00"),
                retail_value=q2(D(qty) * D(p.selling_price)) if qty > 0 else Decimal("0.00"),
                warranty_months=p.warranty_months, gst_rate=p.gst_rate, hsn_code=p.hsn_code,
                track_imei=p.track_imei, is_active=p.is_active, supplier_id=p.supplier_id,
                purchase_date=p.purchase_date, description=p.description,
                status=_status(qty, p.min_stock),
                display=f"{p.name}" + (f" — {p.brand}" if p.brand else "") + (f" {p.model_number}" if p.model_number else ""))


def get_product(product_id: int) -> dict:
    with session_scope() as s:
        p = s.get(Product, product_id)
        if p is None:
            raise NotFoundError("Product not found.")
        return _pdict(p)


def find_product(text: str) -> dict | None:
    """Exact match on barcode or product code (used by barcode scanners)."""
    t = (text or "").strip()
    if not t:
        return None
    with session_scope() as s:
        p = s.scalar(select(Product).where(Product.is_active.is_(True),
                                           or_(Product.barcode == t, func.upper(Product.code) == t.upper())))
        return _pdict(p) if p else None


def list_products(search: str = "", group: str | None = None, category_id: int | None = None,
                  active: bool | None = True, stock_filter: str | None = None, in_stock_only: bool = False) -> list[dict]:
    with session_scope() as s:
        q = select(Product).join(Category).order_by(Product.name)
        if active is not None:
            q = q.where(Product.is_active.is_(active))
        if group:
            q = q.where(Category.group == group)
        if category_id:
            q = q.where(Product.category_id == category_id)
        if in_stock_only:
            q = q.where(Product.quantity > 0)
        if search:
            like = f"%{search.strip()}%"
            q = q.where(or_(Product.name.ilike(like), Product.brand.ilike(like), Product.code.ilike(like),
                            Product.barcode.ilike(like), Product.model_number.ilike(like)))
        rows = [_pdict(p) for p in s.scalars(q).unique()]
        if stock_filter == "LOW":
            rows = [r for r in rows if r["status"] == "LOW"]
        elif stock_filter == "OUT":
            rows = [r for r in rows if r["status"] == "OUT"]
        elif stock_filter == "ALERT":
            rows = [r for r in rows if r["status"] in ("LOW", "OUT")]
        return rows


def stock_summary() -> dict:
    rows = list_products(active=True)
    return dict(
        total_products=len(rows),
        total_quantity=sum(r["quantity"] for r in rows if r["quantity"] > 0),
        stock_value=sum((r["stock_value"] for r in rows), Decimal("0.00")),
        retail_value=sum((r["retail_value"] for r in rows), Decimal("0.00")),
        low_stock=sum(1 for r in rows if r["status"] == "LOW"),
        out_of_stock=sum(1 for r in rows if r["status"] == "OUT"),
    )


# ---------------------------------------------------------------- adjustments
def adjust_stock(product_id: int, qty_change: int, reason: str, *, kind: str | None = None, unit_cost=None,
                 imei_units: list[dict] | None = None, imei_ids_out: list[int] | None = None) -> int:
    """Authorised manual correction. kind: ADJUST_IN / ADJUST_OUT / DAMAGED."""
    require("stock_adjust")
    qty_change = int(qty_change)
    reason = (reason or "").strip()
    if qty_change == 0:
        raise ValidationError("Enter a quantity to add or remove.")
    if not reason:
        raise ValidationError("Please enter a reason for the stock adjustment.")
    kind = kind or ("ADJUST_IN" if qty_change > 0 else "ADJUST_OUT")
    if kind == "DAMAGED" and qty_change > 0:
        raise ValidationError("Damaged write-off must reduce stock.")
    with session_scope() as s:
        p = s.get(Product, product_id)
        if p is None:
            raise NotFoundError("Product not found.")
        bdate = current_business_date(s)
        if p.track_imei:
            if qty_change > 0:
                add_imei_units(s, p, _validate_units(s, imei_units or [], qty_change), source="ADJUSTMENT",
                               cost=unit_cost if unit_cost is not None else p.avg_cost, purchase_date=bdate)
            else:
                ids = list(imei_ids_out or [])
                if len(ids) != -qty_change:
                    raise ValidationError(f"Select exactly {-qty_change} IMEI unit(s) to remove.")
                for rid in ids:
                    r = s.get(ImeiRecord, rid)
                    if r is None or r.product_id != p.id or r.status != "IN_STOCK":
                        raise ValidationError("Selected IMEI is not in stock for this product.")
                    r.status = "DAMAGED" if kind == "DAMAGED" else "REMOVED"
                    r.notes = (r.notes + "\n" if r.notes else "") + f"Removed by adjustment: {reason}"
        mv = apply_movement(s, p, qty_change, kind, business_date=bdate, ref_type="ADJUSTMENT",
                            reference="ADJ", unit_cost=unit_cost if qty_change > 0 else None, notes=reason)
        audit(s, "STOCK_ADJUSTED", "product", p.id, {"code": p.code, "qty_change": qty_change, "kind": kind,
                                                    "reason": reason, "qty_after": p.quantity})
        return mv.id


def product_movements(product_id: int | None = None, start: date | None = None, end: date | None = None,
                      mtype: str | None = None, limit: int = 3000) -> list[dict]:
    with session_scope() as s:
        q = select(StockMovement, Product.name, Product.code).join(Product).order_by(StockMovement.id.desc()).limit(limit)
        if product_id:
            q = q.where(StockMovement.product_id == product_id)
        if start:
            q = q.where(StockMovement.business_date >= start)
        if end:
            q = q.where(StockMovement.business_date <= end)
        if mtype:
            q = q.where(StockMovement.movement_type == mtype)
        out = []
        for mv, name, code in s.execute(q):
            out.append(dict(id=mv.id, business_date=mv.business_date, created_at=mv.created_at, product=name, code=code,
                            type=mv.movement_type, type_label=MOVEMENT_LABELS.get(mv.movement_type, mv.movement_type),
                            qty_in=mv.qty_change if mv.qty_change > 0 else 0,
                            qty_out=-mv.qty_change if mv.qty_change < 0 else 0, qty_after=mv.qty_after,
                            unit_cost=mv.unit_cost, reference=mv.reference, notes=mv.notes))
        return out


def suppliers_for_combo() -> list[dict]:
    with session_scope() as s:
        return [dict(id=x.id, name=x.name) for x in
                s.scalars(select(Supplier).where(Supplier.is_active.is_(True)).order_by(Supplier.name))]
