"""Generic Report -> branded PDF (A4 portrait/landscape, page X of Y)."""
from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table as RLTable,
                                TableStyle)

from .. import APP_SHORT_TITLE
from ..core import settings
from .fmt import dt, format_value
from .pdf_common import (BAD, BRONZE, CHARCOAL, CREAM, FONT, FONT_BOLD, GOLD, GOOD, GREY, LINE, logo_path,
                         register_fonts, shop_lines)
from .report_model import Report


class _NumberedCanvas(rl_canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved = []

    def showPage(self):
        self._saved.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved)
        for state in self._saved:
            self.__dict__.update(state)
            self.setFont(FONT, 7.5)
            self.setFillColor(GREY)
            w = self._pagesize[0]
            self.drawRightString(w - 14 * mm, 9 * mm, f"Page {self._pageNumber} of {total}")
            super().showPage()
        super().save()


def _styles():
    return dict(
        cell=ParagraphStyle("cell", fontName=FONT, fontSize=7.6, leading=9.2, textColor=CHARCOAL),
        cell_r=ParagraphStyle("cellr", fontName=FONT, fontSize=7.6, leading=9.2, alignment=TA_RIGHT, textColor=CHARCOAL),
        head=ParagraphStyle("head", fontName=FONT_BOLD, fontSize=7.6, leading=9.2, textColor=colors.white),
        head_r=ParagraphStyle("headr", fontName=FONT_BOLD, fontSize=7.6, leading=9.2, alignment=TA_RIGHT,
                              textColor=colors.white),
        sec=ParagraphStyle("sec", fontName=FONT_BOLD, fontSize=10.5, leading=13, textColor=CHARCOAL),
        note=ParagraphStyle("note", fontName=FONT, fontSize=7, leading=9, textColor=GREY),
        kv_l=ParagraphStyle("kvl", fontName=FONT, fontSize=8.4, leading=10.5, textColor=GREY),
        kv_v=ParagraphStyle("kvv", fontName=FONT_BOLD, fontSize=8.6, leading=10.5, alignment=TA_RIGHT, textColor=CHARCOAL),
    )


def _esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_report_pdf(report: Report, path: str | Path, shop: dict | None = None) -> Path:
    register_fonts()
    shop = shop or settings.shop_info()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pagesize = landscape(A4) if report.landscape else A4
    st = _styles()
    logo = logo_path(shop)
    header_lines = shop_lines(shop)

    def on_page(c, doc):
        w, h = pagesize
        top = h - 12 * mm
        x = 14 * mm
        if logo:
            c.drawImage(str(logo), x, top - 17 * mm, 17 * mm, 17 * mm, mask="auto", preserveAspectRatio=True)
            x += 20 * mm
        c.setFillColor(CHARCOAL)
        c.setFont(FONT_BOLD, 14)
        c.drawString(x, top - 5 * mm, shop.get("shop_name") or "SDJ Mobile Care")
        c.setFont(FONT, 7.5)
        c.setFillColor(GREY)
        yy = top - 9.5 * mm
        for line in header_lines[:3]:
            c.drawString(x, yy, line)
            yy -= 3.4 * mm
        c.setFillColor(CHARCOAL)
        c.setFont(FONT_BOLD, 12.5)
        c.drawRightString(w - 14 * mm, top - 5 * mm, report.title)
        c.setFont(FONT, 8)
        c.setFillColor(BRONZE)
        if report.subtitle:
            c.drawRightString(w - 14 * mm, top - 9.5 * mm, report.subtitle)
        c.setFillColor(GREY)
        c.setFont(FONT, 7)
        c.drawRightString(w - 14 * mm, top - 13.2 * mm, f"Generated: {dt(report.generated_at)}")
        c.setStrokeColor(GOLD)
        c.setLineWidth(1.4)
        c.line(14 * mm, top - 19.5 * mm, w - 14 * mm, top - 19.5 * mm)
        c.setFont(FONT, 7)
        c.setFillColor(GREY)
        c.drawString(14 * mm, 9 * mm, f"{APP_SHORT_TITLE}")

    doc = SimpleDocTemplate(str(path), pagesize=pagesize, leftMargin=14 * mm, rightMargin=14 * mm,
                            topMargin=36 * mm, bottomMargin=16 * mm, title=report.title,
                            author=shop.get("shop_name") or "SDJ Mobile Care")
    avail = pagesize[0] - 28 * mm
    story = []
    for sec in report.sections:
        block = []
        title_tbl = RLTable([[Paragraph(_esc(sec.title), st["sec"])]], colWidths=[avail])
        title_tbl.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), CREAM),
                                       ("LINEBEFORE", (0, 0), (0, -1), 3, GOLD),
                                       ("LEFTPADDING", (0, 0), (-1, -1), 7), ("TOPPADDING", (0, 0), (-1, -1), 4),
                                       ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
        block.append(title_tbl)
        block.append(Spacer(1, 2.5 * mm))
        if sec.kvs:
            ncol = max(1, sec.kv_columns)
            cells, row = [], []
            styles = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 2.2),
                      ("BOTTOMPADDING", (0, 0), (-1, -1), 2.2)]
            for i, kv in enumerate(sec.kvs):
                vstyle = ParagraphStyle("v", parent=st["kv_v"],
                                        textColor=GOOD if kv.tone == "good" else BAD if kv.tone == "bad" else CHARCOAL,
                                        fontName=FONT_BOLD if kv.strong else FONT)
                lstyle = ParagraphStyle("l", parent=st["kv_l"], fontName=FONT_BOLD if kv.strong else FONT,
                                        textColor=CHARCOAL if kv.strong else GREY)
                row += [Paragraph(_esc(kv.label), lstyle), Paragraph(_esc(format_value(kv.value, kv.kind)), vstyle)]
                if len(row) == ncol * 2:
                    cells.append(row)
                    row = []
            if row:
                row += [""] * (ncol * 2 - len(row))
                cells.append(row)
            colw = []
            for _ in range(ncol):
                colw += [avail / ncol * 0.62, avail / ncol * 0.38]
            for r in range(len(cells)):
                styles.append(("LINEBELOW", (0, r), (-1, r), 0.3, LINE))
            for cidx in range(1, ncol):
                styles.append(("LEFTPADDING", (cidx * 2, 0), (cidx * 2, -1), 14))
            t = RLTable(cells, colWidths=colw)
            t.setStyle(TableStyle(styles))
            block.append(t)
        if sec.table:
            tb = sec.table
            weights = [max(c.width, 0.3) for c in tb.columns]
            total_w = sum(weights)
            colw = [avail * w / total_w for w in weights]
            right = [c.kind in ("money", "int", "pct", "money0") for c in tb.columns]
            data = [[Paragraph(_esc(c.title), st["head_r"] if right[i] else st["head"]) for i, c in enumerate(tb.columns)]]
            for r in tb.rows:
                data.append([Paragraph(_esc(format_value(v, tb.columns[i].kind)), st["cell_r"] if right[i] else st["cell"])
                             for i, v in enumerate(r)])
            if tb.totals:
                bold_r = ParagraphStyle("tr", parent=st["cell_r"], fontName=FONT_BOLD)
                bold_l = ParagraphStyle("tl", parent=st["cell"], fontName=FONT_BOLD)
                data.append([Paragraph(_esc(format_value(v, tb.columns[i].kind)) if v is not None else "",
                                       bold_r if right[i] else bold_l) for i, v in enumerate(tb.totals)])
            if len(data) == 1:
                data.append([Paragraph("No records for this period.", st["note"])] + [""] * (len(tb.columns) - 1))
            t = RLTable(data, colWidths=colw, repeatRows=1)
            ts = [("BACKGROUND", (0, 0), (-1, 0), CHARCOAL), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                  ("TOPPADDING", (0, 0), (-1, -1), 2.6), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.6),
                  ("LEFTPADDING", (0, 0), (-1, -1), 3.5), ("RIGHTPADDING", (0, 0), (-1, -1), 3.5),
                  ("LINEBELOW", (0, 0), (-1, -1), 0.25, LINE)]
            for r in range(1, len(data)):
                if r % 2 == 0:
                    ts.append(("BACKGROUND", (0, r), (-1, r), colors.HexColor("#FBF8EF")))
            if tb.totals and len(tb.rows):
                ts += [("LINEABOVE", (0, -1), (-1, -1), 1, CHARCOAL), ("BACKGROUND", (0, -1), (-1, -1), CREAM)]
            if len(data) == 2 and not tb.rows:
                ts.append(("SPAN", (0, 1), (-1, 1)))
            t.setStyle(TableStyle(ts))
            block.append(t)
        if sec.note:
            block.append(Spacer(1, 1.5 * mm))
            block.append(Paragraph(_esc(sec.note), st["note"]))
        block.append(Spacer(1, 5 * mm))
        if sec.table and len(sec.table.rows) > 18:
            story.extend(block)
        else:
            story.append(KeepTogether(block))
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page, canvasmaker=_NumberedCanvas)
    return path
