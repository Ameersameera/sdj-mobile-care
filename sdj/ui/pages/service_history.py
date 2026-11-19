"""Service history & service reports (daily / weekly / monthly / yearly, technician-wise)."""
from __future__ import annotations

from decimal import Decimal

from PySide6.QtWidgets import QComboBox, QGridLayout, QHBoxLayout, QPushButton

from ... import config
from ...core import repairs, reports
from ...core.repairs import JOB_STATUSES, STATUS_LABELS
from ...output.fmt import inr, qty
from ..widgets import Card, Col, DataTable, DateRangeBar, KpiCard, Page, button, hbox, run_safely, save_file_dialog
from .services import JobDialog


class ServiceHistoryPage(Page):
    subtitle = "All repair jobs with service reports"

    def __init__(self, parent=None):
        super().__init__(parent)
        bar = QHBoxLayout()
        self.range = DateRangeBar("This month")
        self.range.changed.connect(lambda *_: self.refresh())
        for text, preset in (("Today", "Today"), ("This week", "This week"), ("This month", "This month"), ("This year", "This year")):
            b = QPushButton(text)
            b.setProperty("variant", "chip")
            b.clicked.connect(lambda _=False, p=preset: self.range.combo.setCurrentText(p))
            bar.addWidget(b)
        bar.addWidget(self.range)
        bar.addStretch(1)
        bar.addWidget(button("Service report PDF", "", "file-text", lambda: self.export("pdf")))
        bar.addWidget(button("Excel", "", "file-spreadsheet", lambda: self.export("xlsx")))
        self.root.addLayout(bar)
        grid = QGridLayout()
        grid.setSpacing(10)
        self.k = {}
        for i, (key, title, ic) in enumerate([("received", "Jobs received", "clipboard-list"), ("completed", "Jobs completed", "circle-check"),
                                              ("pending", "Jobs pending", "clock"), ("delivered", "Jobs delivered", "check"),
                                              ("labour", "Labour income", "wrench"), ("parts_qty", "Parts consumed", "cpu"),
                                              ("parts_rev", "Spare parts revenue", "indian-rupee"), ("parts_cost", "Spare parts cost", "package"),
                                              ("revenue", "Total service revenue", "trending-up"), ("profit", "Service profit", "badge-percent")]):
            c = KpiCard(title, ic, compact=True)
            self.k[key] = c
            grid.addWidget(c, i // 5, i % 5)
        self.root.addLayout(grid)
        body = QHBoxLayout()
        body.setSpacing(14)
        jobs = Card("Jobs")
        self.status = QComboBox()
        self.status.addItem("All statuses", None)
        for s in JOB_STATUSES:
            self.status.addItem(STATUS_LABELS[s], s)
        self.status.currentIndexChanged.connect(lambda *_: self.refresh())
        self.table = DataTable([Col("job_no", "Job", width=90, bold=True), Col("received_date", "Received", "date", 95),
                                Col("customer_name", "Customer", width=140), Col("customer_phone", "Phone", width=105),
                                Col("device", "Device", width=130), Col("technician", "Technician", width=95),
                                Col("status", "Status", "status", 130), Col("labour", "Labour", "money", 85),
                                Col("parts", "Parts", "money", 85), Col("total", "Total", "money", 95),
                                Col("balance", "Balance", "money", 90)],
                               placeholder="Search job, customer, phone, IMEI…", stretch="customer_name")
        self.table.add_top(self.status)
        self.table.activated.connect(lambda r: (JobDialog(self, r["id"]).exec(), self.refresh()))
        jobs.add(self.table)
        body.addWidget(jobs, 3)
        tech = Card("Technician-wise performance")
        self.tech = DataTable([Col("name", "Technician", width=110, bold=True), Col("completed", "Done", "int", 55),
                               Col("delivered", "Delivered", "int", 70), Col("labour", "Labour", "money", 90),
                               Col("revenue", "Revenue", "money", 95), Col("profit", "Profit", "money", 90)],
                              search=False, stretch="name")
        tech.add(self.tech)
        body.addWidget(tech, 2)
        self.root.addLayout(body, 1)

    def refresh(self):
        a, b = self.range.range()
        fig = reports.period_figures(a, b)
        f, c = fig["f"], fig["counts"]
        self.k["received"].set(qty(c.get("jobs_received", 0)))
        self.k["completed"].set(qty(c.get("jobs_completed", 0)))
        self.k["pending"].set(qty(c.get("jobs_pending", 0)), f"{c.get('jobs_ready', 0)} ready for delivery")
        self.k["delivered"].set(qty(c.get("jobs_delivered", 0)))
        self.k["labour"].set(inr(f["labour"]))
        self.k["parts_qty"].set(qty(c.get("parts_consumed", 0)))
        self.k["parts_rev"].set(inr(f["parts_revenue"]))
        self.k["parts_cost"].set(inr(f["parts_cost"]))
        self.k["revenue"].set(inr(f["service_income"]))
        self.k["profit"].set(inr(f["service_profit"]), "", "good" if f["service_profit"] > 0 else "")
        st = self.status.currentData()
        rows = repairs.list_jobs(statuses=[st] if st else None, start=a, end=b)
        self.table.set_rows(rows)
        self.table.set_footer(f"{len(rows)} job(s) received in this period")
        trows = []
        for name, t in sorted(fig["technicians"].items()):
            rev = t["labour"] + t["parts_rev"]
            trows.append(dict(name=name, completed=t["completed"], delivered=t["delivered"], labour=t["labour"], revenue=rev,
                              profit=rev - t["parts_cost"]))
        self.tech.set_rows(trows)

    def export(self, kind):
        a, b = self.range.range()
        rep = reports.build_service_report(a, b)
        ext = "pdf" if kind == "pdf" else "xlsx"
        path = save_file_dialog(self, "Save service report", str(config.reports_dir() / f"Service_Report_{a}_{b}.{ext}"),
                                "PDF (*.pdf)" if kind == "pdf" else "Excel (*.xlsx)")
        if not path:
            return
        if kind == "pdf":
            from ...output.pdf_report import render_report_pdf
            p = run_safely(self, render_report_pdf, rep, path, success="Service report saved")
            if p:
                from ..printing import show_pdf
                show_pdf(path, self, "Service report")
        else:
            from ...output.excel_export import render_report_excel
            run_safely(self, render_report_excel, rep, path, success="Service report exported")
