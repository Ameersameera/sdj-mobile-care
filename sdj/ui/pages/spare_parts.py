"""Spare parts: stock of repair components and their consumption (counter sales + repairs)."""
from __future__ import annotations

from decimal import Decimal

from PySide6.QtWidgets import QDialog, QTabWidget, QVBoxLayout, QWidget

from ...core import inventory, reports
from ...core.auth import current_user
from ...output.fmt import inr, qty
from ..bus import go
from ..dialogs import ProductDialog
from ..widgets import Card, Col, DataTable, DateRangeBar, KpiCard, Page, button, hbox
from .products import product_columns


class SparePartsPage(Page):
    subtitle = "Displays, batteries, ports and other repair components"

    def __init__(self, parent=None):
        super().__init__(parent)
        u = current_user()
        self.show_cost = u.can("view_profit")
        row = hbox()
        self.k_items = KpiCard("Spare part types", "cpu", compact=True)
        self.k_qty = KpiCard("Units in stock", "layers", compact=True)
        self.k_value = KpiCard("Stock value", "indian-rupee", compact=True)
        self.k_low = KpiCard("Low / out of stock", "triangle-alert", compact=True)
        for k in (self.k_items, self.k_qty, self.k_value, self.k_low):
            if k is self.k_value and not self.show_cost:
                continue
            row.addWidget(k)
        self.root.addLayout(row)
        self.tabs = QTabWidget()
        self.root.addWidget(self.tabs, 1)
        w = QWidget()
        l1 = QVBoxLayout(w)
        l1.setContentsMargins(0, 10, 0, 0)
        c1 = Card()
        self.table = DataTable(product_columns(self.show_cost), placeholder="Search spare parts…", stretch="name")
        c1.add(self.table)
        btns = []
        if u.can("products_edit"):
            btns.append(button("Add spare part", "primary", "package-plus", self.add))
        if u.can("purchases"):
            btns.append(button("Purchase parts", "", "shopping-cart", lambda: go("purchases")))
        btns.append(button("Use in a repair job", "", "wrench", lambda: go("services")))
        btns.append(None)
        c1.add(hbox(*btns))
        l1.addWidget(c1)
        self.tabs.addTab(w, "Spare parts stock")
        w2 = QWidget()
        l2 = QVBoxLayout(w2)
        l2.setContentsMargins(0, 10, 0, 0)
        c2 = Card("Consumption", "service usage counted by the day the part was fitted")
        self.range = DateRangeBar("This month")
        self.range.changed.connect(lambda *_: self.refresh_usage())
        self.usage = DataTable([Col("name", "Spare part", width=240, bold=True), Col("service", "Used in repairs", "int", 110),
                                Col("sold", "Sold at counter", "int", 110), Col("closing", "Stock now", "int", 90),
                                Col("sales_value", "Revenue", "money", 110), Col("cogs", "Cost", "money", 100),
                                Col("gross_profit", "Profit", "money", 100)], search=True, stretch="name")
        self.usage.add_top(self.range)
        c2.add(self.usage)
        l2.addWidget(c2)
        self.tabs.addTab(w2, "Consumption report")
        self.tabs.currentChanged.connect(lambda *_: self.refresh())

    def refresh(self):
        rows = inventory.list_products(group="SPARE")
        self.table.set_rows(rows)
        self.k_items.set(qty(len(rows)))
        self.k_qty.set(qty(sum(max(r["quantity"], 0) for r in rows)))
        self.k_value.set(inr(sum((r["stock_value"] for r in rows), Decimal(0))))
        low = sum(1 for r in rows if r["status"] in ("LOW", "OUT"))
        self.k_low.set(qty(low), "", "warn" if low else "")
        if self.tabs.currentIndex() == 1:
            self.refresh_usage()

    def refresh_usage(self):
        a, b = self.range.range()
        rows = [r for r in reports.product_wise(a, b) if r["group"] == "SPARE"]
        self.usage.set_rows(rows)
        self.usage.set_footer(f"{qty(sum(r['service'] for r in rows))} used in repairs · {qty(sum(r['sold'] for r in rows))} "
                              f"sold at counter · revenue {inr(sum((r['sales_value'] for r in rows), Decimal(0)))}")

    def add(self):
        if ProductDialog(self, group="SPARE").exec() == QDialog.Accepted:
            self.refresh()
