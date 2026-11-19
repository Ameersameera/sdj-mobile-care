"""Invoices, thermal receipts (58/80 mm), service job cards / bills and credit notes (ReportLab)."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .. import config
from ..core import settings
from ..core.calc import amount_in_words
from ..db.base import q2
from .fmt import d_only, dt, inr, qty
from .pdf_common import (BRONZE, CHARCOAL, CREAM, FONT, FONT_BOLD, GOLD, GREY, LINE, logo_path,
                         register_fonts, shop_lines)

MODE_LABELS = {"CASH": "Cash", "UPI": "UPI", "CARD": "Card", "OTHER": "Other", "EXCHANGE": "Exchange credit"}


def _esc(s) -> str:
    return str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>")


def _safe(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)


def upi_link(upi_id: str, shop_name: str, amount, note: str) -> str:
    return (f"upi://pay?pa={quote(upi_id)}&pn={quote(shop_name)}&am={q2(amount)}&cu=INR&tn={quote(note)}")


def _qr_drawing(data: str, size: float) -> Drawing:
    w = QrCodeWidget(data)
    b = w.getBounds()
    bw, bh = b[2] - b[0], b[3] - b[1]
    d = Drawing(size, size, transform=[size / bw, 0, 0, size / bh, 0, 0])
    d.add(w)
    return d


# =============================================================== A4 documents
def _a4_document(path: Path, shop: dict, *, title: str, doc_no: str, doc_date: str, left_title: str,
                 left_lines: list[str], right_title: str, right_lines: list[str], headers: list[str],
                 rows: list[list], col_widths: list[float], right_cols: set[int], totals: list[tuple],
                 words: str = "", extra: list[tuple[str, str]] | None = None, qr: tuple[str, str] | None = None,
                 watermark: str = "", show_logo: bool = True) -> Path:
    register_fonts()
    path.parent.mkdir(parents=True, exist_ok=True)
    logo = logo_path(shop) if show_logo else None
    W, H = A4
    avail = W - 28 * mm

    def on_page(c, _doc):
        top = H - 12 * mm
        x = 14 * mm
        if logo:
            c.drawImage(str(logo), x, top - 24 * mm, 24 * mm, 24 * mm, mask="auto", preserveAspectRatio=True)
            x += 28 * mm
        c.setFillColor(CHARCOAL)
        c.setFont(FONT_BOLD, 18)
        c.drawString(x, top - 7 * mm, shop.get("shop_name") or "SDJ Mobile Care")
        c.setFont(FONT, 8)
        c.setFillColor(BRONZE)
        if shop.get("shop_tagline"):
            c.drawString(x, top - 11.5 * mm, shop["shop_tagline"])
        c.setFillColor(GREY)
        yy = top - 15.5 * mm
        for line in shop_lines(shop)[:4]:
            c.drawString(x, yy, line)
            yy -= 3.6 * mm
        c.setFillColor(GOLD)
        c.setFont(FONT_BOLD, 16)
        c.drawRightString(W - 14 * mm, top - 7 * mm, title)
        c.setFillColor(CHARCOAL)
        c.setFont(FONT_BOLD, 9.5)
        c.drawRightString(W - 14 * mm, top - 13 * mm, doc_no)
        c.setFont(FONT, 8.5)
        c.setFillColor(GREY)
        c.drawRightString(W - 14 * mm, top - 17.5 * mm, doc_date)
        c.setStrokeColor(GOLD)
        c.setLineWidth(1.6)
        c.line(14 * mm, top - 28 * mm, W - 14 * mm, top - 28 * mm)
        if watermark:
            c.saveState()
            c.setFont(FONT_BOLD, 70)
            c.setFillColor(colors.Color(0.75, 0.1, 0.1, alpha=0.13))
            c.translate(W / 2, H / 2)
            c.rotate(35)
            c.drawCentredString(0, 0, watermark)
            c.restoreState()
        c.setFont(FONT, 7)
        c.setFillColor(GREY)
        c.drawCentredString(W / 2, 9 * mm, "This is a computer-generated document.  •  "
                                           f"{shop.get('shop_name') or 'SDJ Mobile Care'}")

    st_l = ParagraphStyle("l", fontName=FONT, fontSize=8.6, leading=11, textColor=CHARCOAL)
    st_lb = ParagraphStyle("lb", fontName=FONT_BOLD, fontSize=8.2, leading=11, textColor=BRONZE)
    st_cell = ParagraphStyle("c", fontName=FONT, fontSize=8.4, leading=10.4, textColor=CHARCOAL)
    st_cell_r = ParagraphStyle("cr", parent=st_cell, alignment=TA_RIGHT)
    st_head = ParagraphStyle("h", fontName=FONT_BOLD, fontSize=8.2, leading=10, textColor=colors.white)
    st_head_r = ParagraphStyle("hr", parent=st_head, alignment=TA_RIGHT)
    st_small = ParagraphStyle("s", fontName=FONT, fontSize=7.4, leading=9.5, textColor=GREY)

    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=44 * mm,
                            bottomMargin=16 * mm, title=f"{title} {doc_no}", author=shop.get("shop_name", ""))
    story = []
    info = Table([[Paragraph(_esc(left_title), st_lb), Paragraph(_esc(right_title), st_lb)],
                  [Paragraph("<br/>".join(_esc(x) for x in left_lines if x), st_l),
                   Paragraph("<br/>".join(_esc(x) for x in right_lines if x), st_l)]],
                 colWidths=[avail * 0.58, avail * 0.42])
    info.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), CREAM), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                              ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 4),
                              ("BOTTOMPADDING", (0, 0), (-1, -1), 4), ("LINEBEFORE", (1, 0), (1, -1), 0.6, LINE)]))
    story += [info, Spacer(1, 5 * mm)]
    data = [[Paragraph(_esc(h), st_head_r if i in right_cols else st_head) for i, h in enumerate(headers)]]
    for r in rows:
        data.append([Paragraph(v if isinstance(v, str) and v.startswith("<") else _esc(v),
                               st_cell_r if i in right_cols else st_cell) for i, v in enumerate(r)])
    tw = sum(col_widths)
    t = Table(data, colWidths=[avail * w / tw for w in col_widths], repeatRows=1)
    ts = [("BACKGROUND", (0, 0), (-1, 0), CHARCOAL), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
          ("LINEBELOW", (0, 0), (-1, -1), 0.3, LINE), ("TOPPADDING", (0, 0), (-1, -1), 4),
          ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]
    t.setStyle(TableStyle(ts))
    story += [t, Spacer(1, 4 * mm)]
    # totals block (right) + words / qr (left)
    tot_rows = []
    for label, value, strong in totals:
        sl = ParagraphStyle("tl", fontName=FONT_BOLD if strong else FONT, fontSize=10.5 if strong else 8.8,
                            leading=13 if strong else 11, textColor=CHARCOAL)
        sv = ParagraphStyle("tv", parent=sl, alignment=TA_RIGHT)
        tot_rows.append([Paragraph(_esc(label), sl), Paragraph(_esc(value), sv)])
    tot_tbl = Table(tot_rows, colWidths=[avail * 0.24, avail * 0.18]) if tot_rows else Spacer(1, 1)
    if tot_rows:
        styles = [("LINEBELOW", (0, 0), (-1, -1), 0.3, LINE), ("TOPPADDING", (0, 0), (-1, -1), 2.5),
                  ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5)]
        for i, (_, _, strong) in enumerate(totals):
            if strong:
                styles += [("BACKGROUND", (0, i), (-1, i), CREAM), ("LINEABOVE", (0, i), (-1, i), 1, GOLD)]
        tot_tbl.setStyle(TableStyle(styles))
    left_parts = []
    if words:
        left_parts.append(Paragraph(f"<b>Amount in words:</b> {_esc(words)}", st_l))
        left_parts.append(Spacer(1, 3 * mm))
    if qr:
        qd = _qr_drawing(qr[0], 26 * mm)
        qt = Table([[qd, Paragraph(_esc(qr[1]), st_small)]], colWidths=[29 * mm, avail * 0.58 - 31 * mm])
        qt.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
        left_parts.append(qt)
    bottom = Table([[left_parts or "", tot_tbl]], colWidths=[avail * 0.58, avail * 0.42])
    bottom.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [bottom, Spacer(1, 6 * mm)]
    for heading, body in extra or []:
        if body:
            story.append(Paragraph(f"<b>{_esc(heading)}</b>", st_lb))
            story.append(Paragraph(_esc(body), st_small))
            story.append(Spacer(1, 2.5 * mm))
    sig = Table([["", Paragraph(f"For <b>{_esc(shop.get('shop_name') or 'SDJ Mobile Care')}</b><br/><br/><br/>"
                                "Authorised Signatory", ParagraphStyle("sig", parent=st_l, alignment=TA_CENTER))]],
                colWidths=[avail * 0.62, avail * 0.38])
    story += [Spacer(1, 6 * mm), sig]
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return path


# =============================================================== thermal engine
class _Thermal:
    def __init__(self, width_mm: int):
        register_fonts()
        self.width_mm = width_mm
        self.W = width_mm * mm
        self.margin = 3 * mm if width_mm >= 80 else 2 * mm
        self.inner = self.W - 2 * self.margin
        self.base = 8.2 if width_mm >= 80 else 7.0
        self.lines: list[tuple] = []

    def add(self, *spec):
        self.lines.append(spec)

    def _height(self, spec) -> float:
        kind = spec[0]
        b = self.base
        if kind == "logo":
            return spec[1] * mm + 2 * mm
        if kind == "title":
            return (b + 4) * 1.35 * len(simpleSplit(spec[1], FONT_BOLD, b + 4, self.inner))
        if kind in ("center", "wrap", "wrapb"):
            size = spec[2] if len(spec) > 2 else b
            font = FONT_BOLD if kind == "wrapb" else FONT
            return size * 1.32 * len(simpleSplit(str(spec[1]), font, size, self.inner))
        if kind == "lr":
            return b * 1.45 if len(spec) < 4 or not spec[3] else (b + 2) * 1.45
        if kind == "sep":
            return b * 0.9
        if kind == "space":
            return spec[1] * mm
        if kind == "qr":
            return spec[2] * mm + 3 * mm
        return b * 1.4

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        height = sum(self._height(s) for s in self.lines) + 12 * mm
        c = rl_canvas.Canvas(str(path), pagesize=(self.W, height))
        y = height - 5 * mm
        b = self.base
        for spec in self.lines:
            kind = spec[0]
            h = self._height(spec)
            if kind == "logo":
                lp = spec[2]
                size = spec[1] * mm
                c.drawImage(str(lp), (self.W - size) / 2, y - size, size, size, mask="auto", preserveAspectRatio=True)
            elif kind == "title":
                c.setFont(FONT_BOLD, b + 4)
                yy = y - (b + 4)
                for ln in simpleSplit(spec[1], FONT_BOLD, b + 4, self.inner):
                    c.drawCentredString(self.W / 2, yy, ln)
                    yy -= (b + 4) * 1.35
            elif kind in ("center", "wrap", "wrapb"):
                size = spec[2] if len(spec) > 2 else b
                font = FONT_BOLD if kind == "wrapb" else FONT
                c.setFont(font, size)
                yy = y - size
                for ln in simpleSplit(str(spec[1]), font, size, self.inner):
                    if kind == "center":
                        c.drawCentredString(self.W / 2, yy, ln)
                    else:
                        c.drawString(self.margin, yy, ln)
                    yy -= size * 1.32
            elif kind == "lr":
                strong = len(spec) > 3 and spec[3]
                size = b + 2 if strong else b
                c.setFont(FONT_BOLD if strong else FONT, size)
                right = str(spec[2])
                rw = c.stringWidth(right, FONT_BOLD if strong else FONT, size)
                left = str(spec[1])
                maxl = self.inner - rw - 2 * mm
                while left and c.stringWidth(left, FONT_BOLD if strong else FONT, size) > maxl:
                    left = left[:-1]
                c.drawString(self.margin, y - size, left)
                c.drawRightString(self.W - self.margin, y - size, right)
            elif kind == "sep":
                c.setDash(1.2, 1.2)
                c.setLineWidth(0.5)
                c.line(self.margin, y - h / 2, self.W - self.margin, y - h / 2)
                c.setDash()
            elif kind == "qr":
                size = spec[2] * mm
                d = _qr_drawing(spec[1], size)
                renderPDF.draw(d, c, (self.W - size) / 2, y - size)
            y -= h
        c.showPage()
        c.save()
        return path


def _thermal_header(t: _Thermal, shop: dict):
    if settings.get("thermal_logo"):
        lp = logo_path(shop)
        if lp:
            t.add("logo", 18 if t.width_mm >= 80 else 14, lp)
    t.add("title", shop.get("shop_name") or "SDJ Mobile Care")
    for line in shop_lines(shop):
        t.add("center", line, t.base - 0.6)
    t.add("sep")


def _footer(t: _Thermal, shop: dict, terms_key: str = "terms"):
    t.add("sep")
    if shop.get("invoice_footer"):
        t.add("center", shop["invoice_footer"], t.base)
    if shop.get("support_info"):
        t.add("center", shop["support_info"], t.base - 0.8)
    if shop.get(terms_key):
        t.add("center", shop[terms_key], t.base - 1.4)
    t.add("space", 2)
    t.add("center", "Powered by SDJ Smart Shop Management System", t.base - 2)


# =============================================================== sales invoice
def invoice_pdf(sale: dict, fmt: str = "A4", path: str | Path | None = None, shop: dict | None = None) -> Path:
    shop = shop or settings.shop_info()
    path = Path(path) if path else config.invoices_dir() / f"Invoice_{_safe(sale['invoice_no'])}_{fmt}.pdf"
    gst = sale.get("gst_enabled")
    title = "TAX INVOICE" if gst else "INVOICE"
    cust = sale.get("customer") or {}
    cancelled = sale.get("status") == "CANCELLED"
    pays = [p for p in sale.get("payments", []) if p["status"] == "ACTIVE" and p["direction"] == "IN"]
    balance = sale.get("balance", Decimal("0"))
    upi = (shop.get("upi_id") or "").strip()
    qr = None
    if upi and balance > 0 and not cancelled:
        qr = (upi_link(upi, shop.get("shop_name", ""), balance, sale["invoice_no"]),
              f"Scan with any UPI app to pay the balance {inr(balance)}\nUPI ID: {upi}")
    if fmt == "A4":
        headers = ["#", "Item", "Qty", "Rate", "Disc."] + (["Taxable", "GST"] if gst else []) + ["Amount"]
        widths = [0.35, 3.6, 0.55, 1.05, 0.9] + ([1.05, 0.95] if gst else []) + [1.15]
        right = {0, 2, 3, 4, 5, 6, 7} if gst else {0, 2, 3, 4, 5}
        rows = []
        for i, ln in enumerate(sale["lines"], 1):
            desc = _esc(ln["product_name"])
            sub = []
            if ln.get("imei"):
                sub.append(f"IMEI/SN: {ln['imei']}")
            if ln.get("warranty_expiry"):
                sub.append(f"Warranty till {d_only(ln['warranty_expiry'])}")
            if ln.get("returned_qty"):
                sub.append(f"Returned: {ln['returned_qty']}")
            if sub:
                desc += f"<br/><font size='7' color='#6B6A5E'>{_esc(' • '.join(sub))}</font>"
            row = [str(i), "<para>" + desc + "</para>", qty(ln["quantity"]), inr(ln["unit_price"]), inr(ln["discount"])]
            if gst:
                row += [inr(ln["taxable"]), f"{ln['gst_rate']:.0f}%\n{inr(ln['gst'])}"]
            row.append(inr(ln["total"]))
            rows.append(row)
        totals = [("Sub total", inr(sale["gross_amount"]), False)]
        disc = sale["item_discount"] + sale["bill_discount"]
        if disc:
            totals.append(("Discount", "-" + inr(disc), False))
        if gst:
            totals.append(("Taxable value", inr(sale["taxable_amount"]), False))
            half = q2(sale["gst_amount"] / 2)
            totals.append(("CGST", inr(half), False))
            totals.append(("SGST", inr(sale["gst_amount"] - half), False))
        if sale["round_off"]:
            totals.append(("Round off", inr(sale["round_off"]), False))
        totals.append(("GRAND TOTAL", inr(sale["grand_total"]), True))
        for p in pays:
            totals.append((f"Paid – {MODE_LABELS.get(p['mode'], p['mode'])}", inr(p["amount"]), False))
        if sale.get("returned"):
            totals.append(("Returned value", "-" + inr(sale["returned"]), False))
        if sale.get("refunded"):
            totals.append(("Refunded", inr(sale["refunded"]), False))
        totals.append(("Balance due", inr(balance), True))
        left = [cust.get("name") or sale.get("customer_name"), f"Ph: {cust['phone']}" if cust.get("phone") else "",
                cust.get("address") or "", f"GSTIN: {cust['gstin']}" if cust.get("gstin") else ""]
        status = "CANCELLED" if cancelled else ("PAID" if balance <= 0 else ("PARTIALLY PAID" if pays else "CREDIT"))
        right_lines = [f"Invoice: {sale['invoice_no']}", f"Date: {dt(sale['created_at'])}", f"Status: {status}",
                       "Prices are inclusive of GST" if gst and sale.get("price_includes_tax") else ""]
        return _a4_document(path, shop, title=title, doc_no=sale["invoice_no"], doc_date=dt(sale["created_at"]),
                            left_title="BILL TO", left_lines=left, right_title="INVOICE DETAILS",
                            right_lines=right_lines, headers=headers, rows=rows, col_widths=widths, right_cols=right,
                            totals=totals, words=amount_in_words(sale["grand_total"]),
                            extra=[("Notes", sale.get("notes", "")), ("Terms & conditions", shop.get("terms", "")),
                                   ("", shop.get("invoice_footer", "")), ("Support", shop.get("support_info", ""))],
                            qr=qr, watermark="CANCELLED" if cancelled else "", show_logo=bool(settings.get("a4_logo")))
    width = 58 if fmt == "THERMAL_58" else 80
    t = _Thermal(width)
    _thermal_header(t, shop)
    t.add("center", title + (" (CANCELLED)" if cancelled else ""), t.base + 1)
    t.add("lr", "Bill No:", sale["invoice_no"])
    t.add("lr", "Date:", dt(sale["created_at"]))
    name = cust.get("name") or sale.get("customer_name")
    if name and not cust.get("is_walk_in"):
        t.add("lr", "Customer:", name)
        if cust.get("phone"):
            t.add("lr", "Phone:", cust["phone"])
    t.add("sep")
    for ln in sale["lines"]:
        t.add("wrapb", ln["product_name"], t.base)
        if ln.get("imei"):
            t.add("wrap", f"IMEI/SN: {ln['imei']}", t.base - 1)
        t.add("lr", f"  {qty(ln['quantity'])} x {inr(ln['unit_price'])}", inr(ln["total"]))
        if ln["discount"]:
            t.add("lr", "  Discount", "-" + inr(ln["discount"]))
        if ln.get("warranty_expiry"):
            t.add("wrap", f"  Warranty till {d_only(ln['warranty_expiry'])}", t.base - 1.2)
    t.add("sep")
    t.add("lr", "Sub total", inr(sale["gross_amount"]))
    disc = sale["item_discount"] + sale["bill_discount"]
    if disc:
        t.add("lr", "Discount", "-" + inr(disc))
    if gst:
        half = q2(sale["gst_amount"] / 2)
        t.add("lr", "Taxable value", inr(sale["taxable_amount"]))
        t.add("lr", "CGST", inr(half))
        t.add("lr", "SGST", inr(sale["gst_amount"] - half))
    if sale["round_off"]:
        t.add("lr", "Round off", inr(sale["round_off"]))
    t.add("lr", "TOTAL", inr(sale["grand_total"]), True)
    for p in pays:
        t.add("lr", f"Paid ({MODE_LABELS.get(p['mode'], p['mode'])})", inr(p["amount"]))
    if sale.get("returned"):
        t.add("lr", "Returned value", "-" + inr(sale["returned"]))
    t.add("lr", "Balance due", inr(balance), balance > 0)
    t.add("wrap", amount_in_words(sale["grand_total"]), t.base - 1.2)
    if qr:
        t.add("sep")
        t.add("qr", qr[0], 30 if width >= 80 else 26)
        t.add("center", f"Scan to pay {inr(balance)} via UPI", t.base - 0.8)
    _footer(t, shop)
    return t.save(path)


# =============================================================== service job card / bill
def job_pdf(job: dict, kind: str = "JOB_CARD", fmt: str = "THERMAL_80", path: str | Path | None = None,
            shop: dict | None = None) -> Path:
    shop = shop or settings.shop_info()
    label = "SERVICE JOB CARD" if kind == "JOB_CARD" else "SERVICE BILL"
    path = Path(path) if path else config.invoices_dir() / f"{'JobCard' if kind == 'JOB_CARD' else 'ServiceBill'}_{_safe(job['job_no'])}_{fmt}.pdf"
    gst = bool(settings.get("gst_enabled"))
    active = [i for i in job.get("items", []) if i["status"] == "ACTIVE"]
    paid = job["paid"]
    balance = job["total"] - paid
    upi = (shop.get("upi_id") or "").strip()
    qr = None
    if upi and kind != "JOB_CARD" and balance > 0:
        qr = (upi_link(upi, shop.get("shop_name", ""), balance, job["job_no"]),
              f"Scan with any UPI app to pay {inr(balance)}\nUPI ID: {upi}")
    if fmt == "A4":
        if kind == "JOB_CARD":
            rows = [["Device", f"{job['brand']} {job['model']}".strip()], ["IMEI / Serial", job.get("imei") or "-"],
                    ["Accessories received", job.get("accessories") or "-"], ["Reported problem", job.get("problem") or "-"],
                    ["Technician", job.get("technician") or "-"],
                    ["Estimated delivery", d_only(job.get("estimated_delivery")) or "-"],
                    ["Estimated labour charge", inr(job["labour"])]]
            headers, widths, right = ["Detail", "Information"], [1.4, 3.6], set()
            totals = [("Advance paid", inr(paid), True)]
        else:
            rows = [["Labour / service charge", "", inr(job["labour"])]]
            for i in active:
                rows.append([i["product_name"], f"{i['quantity']} x {inr(i['unit_price'])}", inr(i["amount"])])
            headers, widths, right = ["Description", "Qty x Rate", "Amount"], [3, 1.4, 1.2], {1, 2}
            totals = []
            if job["discount"]:
                totals.append(("Discount", "-" + inr(job["discount"]), False))
            if gst:
                totals.append(("Taxable value", inr(job["labour_taxable"] + job["parts_taxable"]), False))
                g = job["labour_gst"] + job["parts_gst"]
                totals += [("CGST", inr(q2(g / 2)), False), ("SGST", inr(g - q2(g / 2)), False)]
            totals += [("TOTAL", inr(job["total"]), True), ("Paid", inr(paid), False), ("Balance due", inr(balance), True)]
        left = [job["customer_name"], f"Ph: {job['customer_phone']}" if job.get("customer_phone") else "",
                job.get("customer_address") or ""]
        right_lines = [f"Job No: {job['job_no']}", f"Received: {d_only(job['received_date'])}",
                       f"Status: {job['status_label']}"]
        if job.get("warranty_expiry"):
            right_lines.append(f"Repair warranty till {d_only(job['warranty_expiry'])}")
        return _a4_document(path, shop, title=label, doc_no=job["job_no"], doc_date=dt(datetime.now()),
                            left_title="CUSTOMER", left_lines=left, right_title="JOB DETAILS", right_lines=right_lines,
                            headers=headers, rows=rows, col_widths=widths, right_cols=right, totals=totals,
                            words=amount_in_words(job["total"]) if kind != "JOB_CARD" else "",
                            extra=[("Service terms", shop.get("service_terms", "")), ("", shop.get("invoice_footer", ""))],
                            qr=qr, show_logo=bool(settings.get("a4_logo")))
    width = 58 if fmt == "THERMAL_58" else 80
    t = _Thermal(width)
    _thermal_header(t, shop)
    t.add("center", label, t.base + 1)
    t.add("lr", "Job No:", job["job_no"], True)
    t.add("lr", "Received:", d_only(job["received_date"]))
    t.add("lr", "Customer:", job["customer_name"])
    t.add("lr", "Phone:", job.get("customer_phone") or "")
    t.add("sep")
    t.add("lr", "Device:", f"{job['brand']} {job['model']}".strip())
    if job.get("imei"):
        t.add("lr", "IMEI:", job["imei"])
    if kind == "JOB_CARD":
        if job.get("accessories"):
            t.add("wrap", f"Accessories: {job['accessories']}")
        t.add("wrap", f"Problem: {job.get('problem') or '-'}")
        if job.get("estimated_delivery"):
            t.add("lr", "Est. delivery:", d_only(job["estimated_delivery"]))
        if job["labour"]:
            t.add("lr", "Estimated charge:", inr(job["labour"]))
        t.add("lr", "Advance paid:", inr(paid), True)
        t.add("space", 6)
        t.add("lr", "Customer sign: ____________", "")
    else:
        t.add("sep")
        t.add("lr", "Labour / service", inr(job["labour"]))
        for i in active:
            t.add("wrap", i["product_name"])
            t.add("lr", f"  {i['quantity']} x {inr(i['unit_price'])}", inr(i["amount"]))
        if job["discount"]:
            t.add("lr", "Discount", "-" + inr(job["discount"]))
        if gst:
            g = job["labour_gst"] + job["parts_gst"]
            t.add("lr", "GST (incl.)" if settings.get("prices_include_gst") else "GST", inr(g))
        t.add("lr", "TOTAL", inr(job["total"]), True)
        t.add("lr", "Paid", inr(paid))
        t.add("lr", "Balance", inr(balance), balance > 0)
        if job.get("warranty_expiry"):
            t.add("wrap", f"Repair warranty till {d_only(job['warranty_expiry'])}", t.base - 0.6)
        if qr:
            t.add("qr", qr[0], 28 if width >= 80 else 24)
    _footer(t, shop, "service_terms")
    return t.save(path)


# =============================================================== credit note
def credit_note_pdf(ret: dict, fmt: str = "THERMAL_80", path: str | Path | None = None, shop: dict | None = None) -> Path:
    """ret: {return_no, date, invoice_no, customer, items:[{product,quantity,condition,value}], value, refund, refund_mode, exchange_invoice, reason}"""
    shop = shop or settings.shop_info()
    path = Path(path) if path else config.invoices_dir() / f"CreditNote_{_safe(ret['return_no'])}_{fmt}.pdf"
    if fmt == "A4":
        rows = [[i["product"], i["condition"].title(), qty(i["quantity"]), inr(i["value"])] for i in ret["items"]]
        totals = [("Return value", inr(ret["value"]), True), ("Refund given", inr(ret["refund"]), False)]
        if ret.get("exchange_invoice"):
            totals.append(("Adjusted in exchange", ret["exchange_invoice"], False))
        return _a4_document(path, shop, title="CREDIT NOTE", doc_no=ret["return_no"], doc_date=d_only(ret["date"]),
                            left_title="CUSTOMER", left_lines=[ret.get("customer") or ""], right_title="AGAINST INVOICE",
                            right_lines=[ret["invoice_no"], f"Reason: {ret.get('reason', '')}"],
                            headers=["Item", "Condition", "Qty", "Value"], rows=rows, col_widths=[3, 1.2, 0.6, 1.2],
                            right_cols={2, 3}, totals=totals, words=amount_in_words(ret["value"]))
    t = _Thermal(58 if fmt == "THERMAL_58" else 80)
    _thermal_header(t, shop)
    t.add("center", "CREDIT NOTE / RETURN", t.base + 1)
    t.add("lr", "Credit note:", ret["return_no"])
    t.add("lr", "Date:", d_only(ret["date"]))
    t.add("lr", "Invoice:", ret["invoice_no"])
    t.add("sep")
    for i in ret["items"]:
        t.add("wrap", f"{i['product']} ({i['condition'].lower()})")
        t.add("lr", f"  Qty {i['quantity']}", inr(i["value"]))
    t.add("sep")
    t.add("lr", "Return value", inr(ret["value"]), True)
    t.add("lr", f"Refund ({ret.get('refund_mode') or '-'})", inr(ret["refund"]))
    if ret.get("exchange_invoice"):
        t.add("lr", "Exchange bill", ret["exchange_invoice"])
    t.add("wrap", f"Reason: {ret.get('reason', '')}", t.base - 0.8)
    _footer(t, shop)
    return t.save(path)
