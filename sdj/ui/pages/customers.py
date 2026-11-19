"""Customers: purchase & repair history, ledger, pending balances and payment collection."""
from __future__ import annotations

from decimal import Decimal

from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QTabWidget, QVBoxLayout

from ... import config
from ...core import parties, repairs, sales
from ...output.fmt import inr
from ...output.report_model import Col as RCol
from ...output.report_model import KV, Report, Section, Table
from ..dialogs import CustomerDialog, PaymentDialog, SaleDetailDialog
from ..widgets import (Card, Col, DataTable, KpiCard, Page, button, error, hbox, label, new_ref, run_safely,
                       save_file_dialog)


class CustomerDetail(QDialog):
    def __init__(self, parent, customer_id: int):
        super().__init__(parent)
        self.cid = customer_id
        self.resize(1000, 660)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        self.head = label("", "PageTitle")
        self.sub = label("", "Muted")
        lay.addWidget(self.head)
        lay.addWidget(self.sub)
        row = QHBoxLayout()
        self.k_p = KpiCard("Total purchases", "receipt", compact=True)
        self.k_s = KpiCard("Total service charges", "wrench", compact=True)
        self.k_b = KpiCard("Pending balance", "hand-coins", compact=True)
        for k in (self.k_p, self.k_s, self.k_b):
            row.addWidget(k)
        lay.addLayout(row)
        tabs = QTabWidget()
        self.sales_t = DataTable([Col("created_at", "Date", "datetime", 150), Col("invoice_no", "Invoice", width=140, bold=True),
                                  Col("items", "Items", "int", 60), Col("grand_total", "Total", "money", 100),
                                  Col("paid", "Paid", "money", 100), Col("balance", "Balance", "money", 100),
                                  Col("status", "Status", "status", 100)], search=False, stretch="invoice_no")
        self.sales_t.activated.connect(lambda r: (SaleDetailDialog(self, r["id"]).exec(), self.load()))
        self.jobs_t = DataTable([Col("received_date", "Received", "date", 100), Col("job_no", "Job", width=90, bold=True),
                                 Col("device", "Device", width=150), Col("problem", "Problem", width=200),
                                 Col("status", "Status", "status", 130), Col("total", "Total", "money", 95),
                                 Col("balance", "Balance", "money", 95)], search=False, stretch="problem")
        self.ledger_t = DataTable([Col("date", "Date", "date", 100), Col("particulars", "Particulars", width=260),
                                   Col("ref", "Reference", width=150), Col("debit", "Debit", "money", 100),
                                   Col("credit", "Credit", "money", 100), Col("balance", "Balance", "money", 110, bold=True)],
                                  search=False, stretch="particulars")
        tabs.addTab(self.ledger_t, "Ledger")
        tabs.addTab(self.sales_t, "Purchase history")
        tabs.addTab(self.jobs_t, "Repair history")
        lay.addWidget(tabs, 1)
        lay.addLayout(hbox(button("Edit customer", "", "pencil", self.edit), button("Statement PDF", "", "file-text", self.pdf),
                           None, button("Collect payment", "primary", "hand-coins", self.collect), button("Close", "", None, self.accept)))
        self.load()

    def load(self):
        c = parties.get_customer(self.cid)
        led = parties.customer_ledger(self.cid)
        self.led = led
        rows = parties.list_customers(include_walk_in=True)
        me = next((r for r in rows if r["id"] == self.cid), None) or {}
        self.setWindowTitle(f"Customer – {c['name']}")
        self.head.setText(c["name"])
        self.sub.setText(" · ".join(x for x in (c["phone"], c["address"], c["email"], f"GSTIN {c['gstin']}" if c["gstin"] else "") if x))
        self.k_p.set(inr(me.get("total_purchases", 0)))
        self.k_s.set(inr(me.get("total_service", 0)))
        bal = led["balance"]
        self.k_b.set(inr(max(bal, 0)), f"advance / credit {inr(-bal)}" if bal < 0 else "", "warn" if bal > 0 else "")
        self.sales_t.set_rows(sales.list_sales(customer_id=self.cid))
        self.jobs_t.set_rows(repairs.list_jobs(customer_id=self.cid))
        self.ledger_t.set_rows(led["rows"])

    def edit(self):
        if CustomerDialog(self, customer_id=self.cid).exec() == QDialog.Accepted:
            self.load()

    def collect(self):
        bal = self.led["balance"]
        if bal <= 0:
            error(self, "This customer has no pending balance.")
            return
        dlg = PaymentDialog(self, "Collect payment", bal)
        if dlg.exec() == QDialog.Accepted:
            amt, mode, note = dlg.result
            res = run_safely(self, parties.collect_customer_payment, self.cid, amt, mode, note, new_ref(), topic="sales")
            if res:
                from ..bus import toast
                toast(f"{inr(amt)} collected · applied to " + ", ".join(f"{a['ref']} ({inr(a['amount'])})" for a in res))
                self.load()

    def pdf(self):
        c = self.led["customer"]
        rep = Report(title="Customer Statement", subtitle=f"{c['name']} {c.get('phone', '')}")
        rep.sections.append(Section("Balance", [KV("Total debit", self.led["total_debit"]), KV("Total credit", self.led["total_credit"]),
                                                KV("Balance due", self.led["balance"], strong=True)]))
        rep.sections.append(Section("Ledger", table=Table(
            [RCol("Date", "date"), RCol("Particulars", width=2.5), RCol("Reference", width=1.4), RCol("Debit", "money"),
             RCol("Credit", "money"), RCol("Balance", "money")],
            [[r["date"], r["particulars"], r["ref"], r["debit"], r["credit"], r["balance"]] for r in self.led["rows"]])))
        path = save_file_dialog(self, "Save statement", str(config.reports_dir() / f"Statement_{c['name'].replace(' ', '_')}.pdf"),
                                "PDF (*.pdf)")
        if path:
            from ...output.pdf_report import render_report_pdf
            if run_safely(self, render_report_pdf, rep, path, success="Statement saved"):
                from ..printing import show_pdf
                show_pdf(path, self, "Customer statement")


class CustomersPage(Page):
    subtitle = "Customer accounts, dues and history"

    def __init__(self, parent=None):
        super().__init__(parent)
        row = hbox()
        self.k_count = KpiCard("Customers", "users", compact=True)
        self.k_dues = KpiCard("Total pending", "hand-coins", compact=True)
        self.k_with = KpiCard("Customers with dues", "triangle-alert", compact=True)
        for k in (self.k_count, self.k_dues, self.k_with):
            row.addWidget(k)
        self.root.addLayout(row)
        card = Card()
        self.dues_only = QCheckBox("Only customers with dues")
        self.dues_only.toggled.connect(lambda *_: self.refresh())
        self.table = DataTable([Col("name", "Name", width=200, bold=True), Col("phone", "Phone", width=120),
                                Col("address", "Address", width=200), Col("total_purchases", "Purchases", "money", 115),
                                Col("total_service", "Service", "money", 105),
                                Col("pending", "Pending", "money", 110, color=lambda r: "warn" if r["pending"] > 0 else None),
                                Col("credit", "Advance", "money", 95), Col("last_visit", "Last visit", "date", 100)],
                               placeholder="Search name, phone, address…", stretch="address")
        self.table.add_top(self.dues_only)
        self.table.activated.connect(lambda r: self.open(r["id"]))
        card.add(self.table)
        card.add(hbox(button("Add customer", "primary", "user-plus", self.add),
                      button("Open / ledger", "", "book-open", lambda: self.open((self.table.selected() or {}).get("id"))),
                      button("Collect payment", "", "hand-coins", self.collect),
                      button("Export", "", "file-spreadsheet", lambda: self.table.export(self, "Customers")), None))
        self.root.addWidget(card, 1)

    def refresh(self):
        rows = parties.list_customers(with_dues_only=self.dues_only.isChecked())
        self.table.set_rows(rows)
        allc = parties.list_customers()
        dues = sum((r["pending"] for r in allc), Decimal(0))
        self.k_count.set(str(len(allc)))
        self.k_dues.set(inr(dues), "", "warn" if dues > 0 else "")
        self.k_with.set(str(sum(1 for r in allc if r["pending"] > 0)))

    def open_with(self, payload):
        if isinstance(payload, dict) and payload.get("dues"):
            self.dues_only.setChecked(True)

    def add(self):
        if CustomerDialog(self).exec() == QDialog.Accepted:
            self.refresh()

    def open(self, cid):
        if not cid:
            error(self, "Select a customer first.")
            return
        CustomerDetail(self, cid).exec()
        self.refresh()

    def collect(self):
        r = self.table.selected()
        if not r:
            error(self, "Select a customer first.")
            return
        if r["pending"] <= 0:
            error(self, "This customer has no pending balance.")
            return
        dlg = PaymentDialog(self, f"Collect payment – {r['name']}", r["pending"])
        if dlg.exec() == QDialog.Accepted:
            amt, mode, note = dlg.result
            if run_safely(self, parties.collect_customer_payment, r["id"], amt, mode, note, new_ref(),
                          success=f"{inr(amt)} collected from {r['name']}", topic="sales"):
                self.refresh()
