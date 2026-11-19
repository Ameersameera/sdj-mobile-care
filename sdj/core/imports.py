"""Product import / export with Excel (OpenPyXL)."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

from ..db.base import D
from . import settings
from .errors import BusinessError
from .inventory import create_product, list_categories, list_products

TEMPLATE_HEADERS = ["Name*", "Category*", "Brand", "Model", "Barcode", "Purchase Price", "Selling Price*",
                    "Opening Qty", "Min Stock", "Warranty Months", "GST %", "HSN", "Description"]


def write_template(path: str | Path) -> Path:
    path = Path(path)
    wb = Workbook()
    ws = wb.active
    ws.title = "Products"
    for i, h in enumerate(TEMPLATE_HEADERS, 1):
        c = ws.cell(row=1, column=i, value=h)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1D1F15")
        ws.column_dimensions[c.column_letter].width = 18
    ws.append(["Mobile Charger 20W", "Chargers", "Generic", "FC-20", "", 250, 400, 50, 5, 6, 18, "8504", "Example row – delete me"])
    cats = wb.create_sheet("Categories")
    cats.append(["Category", "Group", "Needs IMEI"])
    for c in list_categories():
        cats.append([c["name"], c["group_label"], "Yes" if c["requires_imei"] else "No"])
    wb.save(path)
    return path


def export_products(path: str | Path) -> Path:
    path = Path(path)
    wb = Workbook()
    ws = wb.active
    ws.title = "Products"
    headers = ["Code", "Name", "Brand", "Category", "Group", "Model", "Barcode", "Purchase Price", "Avg Cost",
               "Selling Price", "Stock", "Min Stock", "Stock Value", "Warranty Months", "GST %", "HSN", "Status"]
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1D1F15")
    for p in list_products(active=None):
        ws.append([p["code"], p["name"], p["brand"], p["category"], p["group_label"], p["model_number"], p["barcode"],
                   float(p["purchase_price"]), float(p["avg_cost"]), float(p["selling_price"]), p["quantity"],
                   p["min_stock"], float(p["stock_value"]), p["warranty_months"], float(p["gst_rate"]), p["hsn_code"],
                   "Active" if p["is_active"] else "Inactive"])
    for col in ("H", "I", "J", "M"):
        for cell in ws[col][1:]:
            cell.number_format = '"₹"#,##,##0.00'
    for i, h in enumerate(headers):
        ws.column_dimensions[ws.cell(row=1, column=i + 1).column_letter].width = max(12, len(h) + 4)
    ws.freeze_panes = "A2"
    wb.save(path)
    return path


def _num(v, default="0") -> Decimal:
    if v is None or str(v).strip() == "":
        return Decimal(default)
    try:
        return D(v)
    except (InvalidOperation, ValueError):
        raise BusinessError(f"'{v}' is not a number")


def import_products(path: str | Path) -> dict:
    wb = load_workbook(path, data_only=True)
    ws = wb.worksheets[0]
    cats = {c["name"].lower(): c for c in list_categories()}
    header = [str(c.value or "").strip().rstrip("*").lower() for c in ws[1]]
    need = ["name", "category", "selling price"]
    if any(n not in header for n in need):
        raise BusinessError("The sheet must have columns: Name, Category, Selling Price (use the template).")
    idx = {h: i for i, h in enumerate(header)}
    created, errors = 0, []
    defaults = {"MOBILE": settings.get("default_gst_mobile"), "ACCESSORY": settings.get("default_gst_accessory"),
                "SPARE": settings.get("default_gst_spare")}
    for r_no, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if row is None or all(v in (None, "") for v in row):
            continue

        def get(col):
            i = idx.get(col)
            return row[i] if i is not None and i < len(row) else None
        try:
            name = str(get("name") or "").strip()
            if not name:
                raise BusinessError("name is empty")
            if "delete me" in str(get("description") or "").lower():
                continue
            cat = cats.get(str(get("category") or "").strip().lower())
            if cat is None:
                raise BusinessError(f"unknown category '{get('category')}'")
            qty = int(_num(get("opening qty")))
            if cat["requires_imei"] and qty:
                raise BusinessError("phones need IMEI numbers – import with Opening Qty 0 and add stock via Purchases")
            gst = get("gst %")
            data = dict(name=name, category_id=cat["id"], brand=str(get("brand") or ""), model_number=str(get("model") or ""),
                        barcode=str(get("barcode") or "").strip() or None, purchase_price=_num(get("purchase price")),
                        selling_price=_num(get("selling price")), min_stock=int(_num(get("min stock"),
                                                                                       str(settings.get("low_stock_default") or 0))),
                        warranty_months=int(_num(get("warranty months"))),
                        gst_rate=_num(gst, str(defaults[cat["group"]] or 0)), hsn_code=str(get("hsn") or ""),
                        description=str(get("description") or ""), track_imei=cat["requires_imei"])
            create_product(data, opening_qty=qty, opening_cost=data["purchase_price"])
            created += 1
        except Exception as exc:  # collect row errors, keep importing the rest
            errors.append((r_no, str(exc)))
    return dict(created=created, errors=errors)
