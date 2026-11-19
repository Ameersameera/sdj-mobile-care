"""Shared ReportLab setup: Unicode fonts (₹ support), brand colours, logo."""
from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from .. import config

# Brand palette taken from the SDJ logo
CHARCOAL = colors.HexColor("#1D1F15")
GOLD = colors.HexColor("#CFAA51")
BRONZE = colors.HexColor("#8C6D31")
CREAM = colors.HexColor("#F6EFD9")
CREAM_DARK = colors.HexColor("#E7D59E")
GREY = colors.HexColor("#6B6A5E")
LINE = colors.HexColor("#D9D2BC")
GOOD = colors.HexColor("#1E7B34")
BAD = colors.HexColor("#B42318")

FONT = "SDJSans"
FONT_BOLD = "SDJSans-Bold"
MONO = "SDJMono"
MONO_BOLD = "SDJMono-Bold"
_registered = False


def _font_file(name: str) -> Path:
    p = config.asset_path("fonts", name)
    if p.exists():
        return p
    import matplotlib
    return Path(matplotlib.get_data_path()) / "fonts" / "ttf" / name


def register_fonts() -> None:
    global _registered
    if _registered:
        return
    pdfmetrics.registerFont(TTFont(FONT, str(_font_file("DejaVuSans.ttf"))))
    pdfmetrics.registerFont(TTFont(FONT_BOLD, str(_font_file("DejaVuSans-Bold.ttf"))))
    pdfmetrics.registerFont(TTFont(MONO, str(_font_file("DejaVuSansMono.ttf"))))
    pdfmetrics.registerFont(TTFont(MONO_BOLD, str(_font_file("DejaVuSansMono-Bold.ttf"))))
    from reportlab.pdfbase.pdfmetrics import registerFontFamily
    registerFontFamily(FONT, normal=FONT, bold=FONT_BOLD, italic=FONT, boldItalic=FONT_BOLD)
    _registered = True


def logo_path(shop: dict | None = None) -> Path | None:
    custom = (shop or {}).get("shop_logo_path") or ""
    if custom and Path(custom).exists():
        return Path(custom)
    p = config.asset_path("logo_print.png")          # 240 px – sharp at print size, keeps PDFs small
    if not p.exists():
        p = config.asset_path("logo.png")
    return p if p.exists() else None


def shop_lines(shop: dict) -> list[str]:
    """Address / contact lines – blank settings are simply skipped (nothing invented)."""
    lines = []
    if shop.get("shop_address"):
        lines.extend([x.strip() for x in str(shop["shop_address"]).splitlines() if x.strip()])
    contact = " | ".join(x for x in (f"Ph: {shop['shop_phone']}" if shop.get("shop_phone") else "",
                                     shop.get("shop_email") or "") if x)
    if contact:
        lines.append(contact)
    if shop.get("shop_gstin"):
        lines.append(f"GSTIN: {shop['shop_gstin']}")
    return lines
