"""Key/value application settings stored in the database (JSON values)."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select

from ..db.base import session_scope
from ..db.models import Setting

EXPENSE_CATEGORIES = ["Rent", "Electricity", "Salary", "Transportation", "Food", "Internet",
                      "Maintenance", "Packaging", "Miscellaneous"]

DEFAULTS: dict[str, Any] = {
    # shop / branding  (address, phone and GSTIN are intentionally blank – owner fills them)
    "shop_name": "SDJ Mobile Care",
    "shop_tagline": "Mobiles • Accessories • Service",
    "shop_address": "",
    "shop_phone": "",
    "shop_email": "",
    "shop_gstin": "",
    "shop_logo_path": "",          # blank = bundled logo
    "invoice_footer": "Thank you for shopping at SDJ Mobile Care!",
    "support_info": "",
    "terms": "Goods once sold are returned or exchanged only as per shop policy. "
             "Warranty is as per manufacturer terms.",
    "service_terms": "Please collect your device within 30 days of the delivery date. "
                     "The shop is not responsible for data loss or for faults not related to the repair.",
    "upi_id": "",
    # invoice & tax
    "invoice_prefix": "SDJ",
    "credit_note_prefix": "CN",
    "job_prefix": "JOB",
    "purchase_prefix": "PUR",
    "gst_enabled": False,
    "prices_include_gst": True,
    "default_gst_mobile": 18,
    "default_gst_accessory": 18,
    "default_gst_spare": 18,
    "service_gst_rate": 18,
    "round_off": True,
    # printing
    "default_print_format": "THERMAL_80",   # THERMAL_80 / THERMAL_58 / A4
    "print_after_save": "ASK",             # ASK / PRINT / PREVIEW / NONE
    "printer_name": "",
    "thermal_logo": False,
    "a4_logo": True,
    # stock
    "low_stock_default": 5,
    # service
    "technicians": [],
    "default_repair_warranty_days": 0,
    # cash
    "default_opening_cash": 0,
    # staff controls
    "owner_approval": True,         # staff without a permission can get owner / manager approval on the spot
    "staff_discount_limit": 0,      # % of the bill; above this staff need approval (0 = no limit)
    # backup
    "auto_backup": True,
    "backup_dir": "",
    "backup_keep": 30,
    "last_backup_at": "",
    # ui
    "theme": "light",
    "expense_categories": EXPENSE_CATEGORIES,
    "fy_start_month": 4,
}

_cache: dict[str, Any] = {}


def clear_cache() -> None:
    _cache.clear()


def _load() -> None:
    if _cache:
        return
    with session_scope() as s:
        for row in s.scalars(select(Setting)):
            try:
                _cache[row.key] = json.loads(row.value) if row.value is not None else None
            except json.JSONDecodeError:
                _cache[row.key] = row.value
    _cache.setdefault("__loaded__", True)


def get(key: str, default: Any = None) -> Any:
    _load()
    if key in _cache:
        return _cache[key]
    if default is not None:
        return default
    return DEFAULTS.get(key)


def get_all() -> dict[str, Any]:
    _load()
    out = dict(DEFAULTS)
    out.update({k: v for k, v in _cache.items() if not k.startswith("__")})
    return out


def set_many(values: dict[str, Any], session=None) -> None:
    def _apply(s):
        for k, v in values.items():
            row = s.get(Setting, k)
            payload = json.dumps(v)
            if row is None:
                s.add(Setting(key=k, value=payload))
            else:
                row.value = payload
    if session is not None:
        _apply(session)
    else:
        with session_scope() as s:
            _apply(s)
    clear_cache()


def set_value(key: str, value: Any) -> None:
    set_many({key: value})


def shop_info() -> dict[str, Any]:
    """Shop details used on invoices and reports (blank fields are skipped)."""
    a = get_all()
    return {k: a.get(k, "") for k in ("shop_name", "shop_tagline", "shop_address", "shop_phone",
                                      "shop_email", "shop_gstin", "invoice_footer", "support_info",
                                      "terms", "service_terms", "upi_id", "shop_logo_path")}
