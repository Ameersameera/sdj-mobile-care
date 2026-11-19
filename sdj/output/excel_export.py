"""Report -> Excel workbook (OpenPyXL) and CSV."""
from __future__ import annotations

import csv
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from ..core import settings
from .fmt import format_value
from .report_model import Report

MONEY_FMT = '"₹"#,##,##0.00;[Red]-"₹"#,##,##0.00'
INT_FMT = '#,##,##0'
HEAD_FILL = PatternFill("solid", fgColor="1D1F15")
SEC_FILL = PatternFill("solid", fgColor="F6EFD9")
TOT_FILL = PatternFill("solid", fgColor="E7D59E")
THIN = Side(style="thin", color="D9D2BC")


def _cell_value(v, kind):
    if v is None:
        return None
    if kind in ("money", "money0") and isinstance(v, (Decimal, int, float)):
        return float(v)
    if kind == "int" and isinstance(v, (int, Decimal)):
        return int(v)
    if isinstance(v, (date, datetime)):
        return v
    if isinstance(v, Decimal):
        return float(v)
    return v


def _sheet_title(title: str, used: set[str]) -> str:
    bad = '[]:*?/\\'
    t = "".join(ch for ch in title if ch not in bad)[:28] or "Sheet"
    base, n = t, 2
    while t in used:
        t = f"{base[:25]}_{n}"
        n += 1
    used.add(t)
    return t


def render_report_excel(report: Report, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    used = {"Summary"}
    shop = settings.shop_info()
    ws["A1"] = shop.get("shop_name") or "SDJ Mobile Care"
    ws["A1"].font = Font(size=16, bold=True, color="1D1F15")
    ws["A2"] = report.title
    ws["A2"].font = Font(size=13, bold=True, color="8C6D31")
    ws["A3"] = report.subtitle
    ws["A4"] = f"Generated: {report.generated_at:%d %b %Y %I:%M %p}"
    ws["A4"].font = Font(size=9, color="6B6A5E")
    row = 6
    for sec in report.sections:
        if not sec.kvs:
            continue
        ws.cell(row=row, column=1, value=sec.title).font = Font(bold=True, size=12)
        ws.cell(row=row, column=1).fill = SEC_FILL
        ws.cell(row=row, column=2).fill = SEC_FILL
        row += 1
        for kv in sec.kvs:
            ws.cell(row=row, column=1, value=kv.label).font = Font(bold=kv.strong)
            c = ws.cell(row=row, column=2, value=_cell_value(kv.value, kv.kind))
            c.font = Font(bold=kv.strong, color="1E7B34" if kv.tone == "good" else "B42318" if kv.tone == "bad" else "000000")
            if kv.kind in ("money", "money0"):
                c.number_format = MONEY_FMT
            elif kv.kind == "int":
                c.number_format = INT_FMT
            row += 1
        row += 1
    ws.column_dimensions["A"].width = 44
    ws.column_dimensions["B"].width = 22
    for sec in report.sections:
        if not sec.table:
            continue
        tws = wb.create_sheet(_sheet_title(sec.title, used))
        tb = sec.table
        tws.cell(row=1, column=1, value=f"{report.title} – {sec.title}").font = Font(bold=True, size=12)
        tws.cell(row=2, column=1, value=report.subtitle).font = Font(size=9, color="6B6A5E")
        hr = 4
        for i, col in enumerate(tb.columns, 1):
            c = tws.cell(row=hr, column=i, value=col.title)
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = HEAD_FILL
            c.alignment = Alignment(horizontal="right" if col.kind in ("money", "int", "pct") else "left", vertical="center")
        for r_i, r in enumerate(tb.rows, hr + 1):
            for i, (col, v) in enumerate(zip(tb.columns, r), 1):
                c = tws.cell(row=r_i, column=i, value=_cell_value(v, col.kind))
                c.border = Border(bottom=THIN)
                if col.kind in ("money", "money0"):
                    c.number_format = MONEY_FMT
                elif col.kind == "int":
                    c.number_format = INT_FMT
                elif col.kind == "date":
                    c.number_format = "dd-mmm-yyyy"
        if tb.totals:
            tr = hr + len(tb.rows) + 1
            for i, (col, v) in enumerate(zip(tb.columns, tb.totals), 1):
                c = tws.cell(row=tr, column=i, value=_cell_value(v, col.kind))
                c.font = Font(bold=True)
                c.fill = TOT_FILL
                if col.kind in ("money", "money0"):
                    c.number_format = MONEY_FMT
                elif col.kind == "int":
                    c.number_format = INT_FMT
        for i, col in enumerate(tb.columns, 1):
            width = max([len(col.title)] + [len(format_value(r[i - 1], col.kind)) for r in tb.rows[:300]] + [8])
            tws.column_dimensions[get_column_letter(i)].width = min(width + 3, 48)
        tws.freeze_panes = tws.cell(row=hr + 1, column=1)
    wb.save(path)
    return path


def render_report_csv(report: Report, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow([settings.get("shop_name") or "SDJ Mobile Care"])
        w.writerow([report.title, report.subtitle])
        w.writerow([])
        for sec in report.sections:
            w.writerow([f"## {sec.title}"])
            for kv in sec.kvs:
                w.writerow([kv.label, format_value(kv.value, kv.kind).replace("₹", "")])
            if sec.table:
                w.writerow([c.title for c in sec.table.columns])
                for r in sec.table.rows:
                    w.writerow([format_value(v, c.kind).replace("₹", "") for v, c in zip(r, sec.table.columns)])
                if sec.table.totals:
                    w.writerow([format_value(v, c.kind).replace("₹", "") if v is not None else ""
                                for v, c in zip(sec.table.totals, sec.table.columns)])
            w.writerow([])
    return path


def rows_to_excel(path: str | Path, title: str, headers: list[str], rows: list[list], money_cols: set[int] = frozenset()) -> Path:
    """Simple table export used by list screens."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = title[:28] or "Data"
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=i, value=h)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = HEAD_FILL
    for r_i, r in enumerate(rows, 2):
        for i, v in enumerate(r, 1):
            c = ws.cell(row=r_i, column=i, value=_cell_value(v, "money" if (i - 1) in money_cols else "text"))
            if (i - 1) in money_cols:
                c.number_format = MONEY_FMT
    for i, h in enumerate(headers, 1):
        ws.column_dimensions[get_column_letter(i)].width = min(max(len(h) + 4, 12), 42)
    ws.freeze_panes = "A2"
    wb.save(path)
    return path
