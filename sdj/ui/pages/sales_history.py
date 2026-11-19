"""Sales history: search invoices / customers / IMEI, view, reprint, collect dues, cancel, return."""
from __future__ import annotations

from decimal import Decimal

from PySide6.QtWidgets import QComboBox

from ...core import sales
from ...core.auth import current_user
from ...output.fmt import inr
from ..dialogs import SaleDetailDialog
from ..widgets import Card, Col, DataTable, DateRangeBar, Page, button, hbox


class SalesHistoryPage(Page):
    subtitle = "Every invoice ever made – nothing is deleted; cancelled bills stay visible"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.range = DateRangeBar("This month")
        self.status = QComboBox()
        self.status.addItems(["All bills", "Completed", "Cancelled", "With balance due"])
        self.range.changed.connect(lambda *_: self.refresh())
        self.status.currentIndexChanged.connect(lambda *_: self.refresh())
        card = Card()
        self.table = DataTable([Col("created_at", "Date & time", "datetime", 160), Col("invoice_no", "Invoice", width=150, bold=True),
                                Col("customer_name", "Customer", width=180), Col("customer_phone", "Phone", width=110),
                                Col("sale_type", "Type", width=80), Col("items", "Items", "int", 60),
                                Col("grand_total", "Total", "money", 110), Col("paid", "Paid", "money", 110),
                                Col("returned", "Returned", "money", 100), Col("balance", "Balance", "money", 100,
                                                                               color=lambda r: "warn" if r["balance"] > 0 else None),
                                Col("status", "Status", "status", 110)],
                               placeholder="Search invoice no, customer, phone, product or IMEI…", stretch="customer_name")
        self.table.search.returnPressed.connect(self.refresh)
        self.table.add_top(self.status)
        self.table.add_top(self.range)
        self.table.activated.connect(self.open_sale)
        card.add(self.table)
        bar = hbox(button("View / reprint", "primary", "eye", self._view),
                   button("Export to Excel", "", "file-spreadsheet", lambda: self.table.export(self, "Sales")), None)
        card.add(bar)
        self.root.addWidget(card, 1)

    def refresh(self):
        a, b = self.range.range()
        st = self.status.currentText()
        rows = sales.list_sales(a, b, search=self.table.search.text() if len(self.table.search.text()) >= 3 else "",
                                status="COMPLETED" if st in ("Completed", "With balance due") else
                                ("CANCELLED" if st == "Cancelled" else None))
        if st == "With balance due":
            rows = [r for r in rows if r["balance"] > 0]
        self.table.set_rows(rows)
        done = [r for r in rows if r["status"] == "COMPLETED"]
        self.table.set_footer(f"{len(rows)} bills · sales {inr(sum((r['grand_total'] - r['returned'] for r in done), Decimal(0)))} "
                              f"· collected {inr(sum((r['paid'] for r in done), Decimal(0)))} · pending "
                              f"{inr(sum((r['balance'] for r in done), Decimal(0)))}")

    def _view(self):
        r = self.table.selected()
        if r:
            self.open_sale(r)

    def open_sale(self, row):
        SaleDetailDialog(self, row["id"]).exec()
        self.refresh()

    def open_with(self, payload):
        if isinstance(payload, dict) and payload.get("sale_id"):
            SaleDetailDialog(self, payload["sale_id"]).exec()
