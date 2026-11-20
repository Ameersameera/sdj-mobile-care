"""One page with the backup recovery code, to print and keep away from the PC."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from .pdf_common import BRONZE, CHARCOAL, FONT, FONT_BOLD, GREY, LINE, MONO_BOLD, logo_path, register_fonts


def recovery_code_pdf(code: str, shop: dict, path: str | Path) -> Path:
    register_fonts()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    w, h = A4
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setTitle("SDJ Mobile Care – backup recovery code")
    y = h - 30 * mm
    logo = logo_path(shop)
    if logo:
        try:
            c.drawImage(str(logo), 20 * mm, y - 6 * mm, 22 * mm, 22 * mm, preserveAspectRatio=True, mask="auto")
        except Exception:  # a broken logo never stops the page
            pass
    c.setFillColor(CHARCOAL)
    c.setFont(FONT_BOLD, 17)
    c.drawString(48 * mm, y + 8 * mm, shop.get("shop_name") or "SDJ Mobile Care")
    c.setFont(FONT, 11)
    c.setFillColor(GREY)
    c.drawString(48 * mm, y + 1 * mm, "Backup recovery code")
    y -= 24 * mm
    c.setStrokeColor(LINE)
    c.setLineWidth(1.2)
    c.roundRect(20 * mm, y - 22 * mm, w - 40 * mm, 30 * mm, 4 * mm)
    c.setFillColor(CHARCOAL)
    c.setFont(MONO_BOLD, 24)
    c.drawCentredString(w / 2, y - 10 * mm, code)
    y -= 36 * mm
    c.setFont(FONT, 10.5)
    c.setFillColor(CHARCOAL)
    lines = [
        f"Created on {datetime.now():%d %b %Y, %I:%M %p}.",
        "",
        "What it is for:",
        "•  Every backup made from now on (on the phone, the 2nd backup folder, Google Drive) is locked",
        "    with the owner's password. This code opens them too – even if the password is forgotten.",
        "•  On a new PC: Restore from phone / Restore from a backup file → type this code instead of the password.",
        "",
        "Keep it safe:",
        "•  Keep this paper away from the shop PC (at home, in a locker).",
        "•  Anyone who has this code AND a backup file can open the shop's data. Do not share a photo of it.",
        "•  If you make a new recovery code, backups made after that use the new code (older backups keep this one).",
    ]
    for line in lines:
        if line.endswith(":") and not line.startswith("•"):
            c.setFont(FONT_BOLD, 11)
            c.setFillColor(BRONZE)
        else:
            c.setFont(FONT, 10.5)
            c.setFillColor(CHARCOAL)
        c.drawString(20 * mm, y, line)
        y -= 6.2 * mm
    c.setFont(FONT, 8.5)
    c.setFillColor(GREY)
    c.drawString(20 * mm, 15 * mm, "SDJ Mobile Care – Smart Shop Management System")
    c.showPage()
    c.save()
    return path
