"""Monthly and yearly reports (custom ranges too) with PDF / Excel / CSV export."""
from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QRadioButton, QSpinBox

from ... import config
from ...core import reports
from ...output.fmt import inr
from ..charts import ChartWidget
from ..printing import show_pdf
from ..widgets import Card, DateEdit, Page, ReportView, button, hbox, run_safely, save_file_dialog

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


class _ReportPage(Page):
    file_stem = "Report"

    def _export_bar(self):
        return [button("PDF", "", "file-text", lambda: self.export("pdf")),
                button("Excel", "", "file-spreadsheet", lambda: self.export("xlsx")),
                button("CSV", "", "download", lambda: self.export("csv"))]

    def export(self, kind):
        rep = getattr(self, "report", None)
        if rep is None:
            return
        ext = {"pdf": "pdf", "xlsx": "xlsx", "csv": "csv"}[kind]
        filt = {"pdf": "PDF (*.pdf)", "xlsx": "Excel (*.xlsx)", "csv": "CSV (*.csv)"}[kind]
        path = save_file_dialog(self, "Export report", str(config.reports_dir() / f"{self.file_stem}.{ext}"), filt)
        if not path:
            return
        if kind == "pdf":
            from ...output.pdf_report import render_report_pdf
            if run_safely(self, render_report_pdf, rep, path, success="PDF saved"):
                show_pdf(path, self, rep.title)
        elif kind == "xlsx":
            from ...output.excel_export import render_report_excel
            run_safely(self, render_report_excel, rep, path, success="Excel saved")
        else:
            from ...output.excel_export import render_report_csv
            run_safely(self, render_report_csv, rep, path, success="CSV saved")


class MonthlyReportsPage(_ReportPage):
    subtitle = "All figures are calculated from real transactions"

    def __init__(self, parent=None):
        super().__init__(parent)
        t = date.today()
        self.month = QComboBox()
        self.month.addItems(MONTHS)
        self.month.setCurrentIndex(t.month - 1)
        self.year = QSpinBox()
        self.year.setRange(2020, 2100)
        self.year.setValue(t.year)
        self.custom = QCheckBox("Custom dates")
        self.start = DateEdit(t.replace(day=1))
        self.end = DateEdit(t)
        self.start.setEnabled(False)
        self.end.setEnabled(False)
        self.custom.toggled.connect(lambda on: (self.start.setEnabled(on), self.end.setEnabled(on), self.month.setEnabled(not on),
                                                self.year.setEnabled(not on)))
        bar = Card()
        bar.add(hbox(QLabel("Month"), self.month, self.year, 16, self.custom, self.start, QLabel("to"), self.end,
                     button("Generate", "primary", "refresh-cw", self.refresh), None, *self._export_bar()))
        self.root.addWidget(bar)
        self.view = ReportView()
        self.root.addWidget(self.view, 1)

    def _range(self):
        if self.custom.isChecked():
            return self.start.value(), self.end.value()
        y, m = self.year.value(), self.month.currentIndex() + 1
        a = date(y, m, 1)
        b = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))
        return a, min(b, max(a, date.today()))

    def refresh(self):
        a, b = self._range()
        title = "Monthly Report" if not self.custom.isChecked() else "Business Report"
        self.report = reports.build_period_report(a, b, title)
        self.file_stem = f"{title.replace(' ', '_')}_{a}_{b}"
        self.view.set_report(self.report)


class YearlyReportsPage(_ReportPage):
    subtitle = "Calendar year or Indian financial year (April – March)"

    def __init__(self, parent=None):
        super().__init__(parent)
        t = date.today()
        self.year = QSpinBox()
        self.year.setRange(2020, 2100)
        self.fy = QRadioButton("Financial year (Apr–Mar)")
        self.cy = QRadioButton("Calendar year")
        self.fy.setChecked(True)
        self.year.setValue(t.year if t.month >= 4 else t.year - 1)
        bar = Card()
        bar.add(hbox(QLabel("Year starting"), self.year, 12, self.fy, self.cy,
                     button("Generate", "primary", "refresh-cw", self.refresh), None, *self._export_bar()))
        self.root.addWidget(bar)
        chart_card = Card("Month-wise revenue and net profit")
        self.chart = ChartWidget(height=230)
        chart_card.add(self.chart)
        self.root.addWidget(chart_card)
        self.view = ReportView()
        self.root.addWidget(self.view, 1)
        self.fy.toggled.connect(lambda *_: self.refresh())

    def refresh(self):
        fin = self.fy.isChecked()
        self.report = reports.build_yearly_report(self.year.value(), fin)
        self.file_stem = f"Yearly_Report_{'FY' if fin else 'CY'}{self.year.value()}"
        self.view.set_report(self.report)
        sec = self.report.section("Month-wise summary")
        if sec and sec.table:
            labels = [r[0][:3] + " " + r[0][-2:] for r in sec.table.rows]
            self.chart.grouped(labels, [("Revenue", [r[3] for r in sec.table.rows]),
                                        ("Net operating profit", [r[7] for r in sec.table.rows])])
