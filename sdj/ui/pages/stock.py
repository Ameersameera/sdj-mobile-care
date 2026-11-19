"""Stock management: live stock & valuation, low/out-of-stock alerts, movement history, authorised adjustments."""
from __future__ import annotations

from decimal import Decimal

from PySide6.QtWidgets import QComboBox, QDialog, QLabel, QLineEdit, QTabWidget, QVBoxLayout, QWidget

from ...core import inventory
from ...core.auth import current_user
from ...core.inventory import MOVEMENT_LABELS
from ...output.fmt import inr, qty
from ..dialogs import ImeiEntryDialog, ImeiSelectDialog
from ..widgets import (Card, Col, DataTable, DateRangeBar, FormDialog, KpiCard, MoneySpin, Page, ProductPicker,
                       QtySpin, button, error, hbox, label, run_safely)


class AdjustDialog(FormDialog):
    def __init__(self, parent, product: dict):
        super().__init__(parent, f"Adjust stock – {product['name']}", 480)
        self.p = product
        self.form.addRow(label(f"Current stock: {product['quantity']}  ·  Avg cost {inr(product['avg_cost'])}", "SectionTitle"))
        self.kind = QComboBox()
        self.kind.addItem("Add stock (found / correction)", "ADJUST_IN")
        self.kind.addItem("Remove stock (lost / correction)", "ADJUST_OUT")
        self.kind.addItem("Damaged – write off", "DAMAGED")
        self.qty = QtySpin()
        self.cost = MoneySpin()
        self.cost.set_d(product["avg_cost"] or product["purchase_price"])
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("Required – e.g. 'Physical count on 1st'")
        self.form.addRow("Type", self.kind)
        self.form.addRow("Quantity", self.qty)
        self.cost_row = QLabel("Unit cost")
        self.form.addRow(self.cost_row, self.cost)
        self.form.addRow("Reason *", self.reason)
        self.form.addRow(label("Adjustments are recorded in the audit log with your name.", "Muted"))
        self.kind.currentIndexChanged.connect(self._kind)
        self.finish_layout()
        self._kind()

    def _kind(self):
        add = self.kind.currentData() == "ADJUST_IN"
        self.cost.setVisible(add)
        self.cost_row.setVisible(add)
        self.qty.setMaximum(99999 if add else max(self.p["quantity"], 1))

    def on_save(self):
        kind = self.kind.currentData()
        n = self.qty.value()
        units, ids = None, None
        if self.p["track_imei"]:
            if kind == "ADJUST_IN":
                d = ImeiEntryDialog(self, n)
                if d.exec() != QDialog.Accepted:
                    return False
                units = d.units
            else:
                d = ImeiSelectDialog(self, self.p, n)
                if d.exec() != QDialog.Accepted:
                    return False
                ids = [r["id"] for r in d.selected]
        change = n if kind == "ADJUST_IN" else -n
        inventory.adjust_stock(self.p["id"], change, self.reason.text(), kind=kind,
                               unit_cost=self.cost.value_d() if kind == "ADJUST_IN" else None,
                               imei_units=units, imei_ids_out=ids)


class StockPage(Page):
    subtitle = "Stock is updated automatically by sales, purchases, returns and repairs"

    def __init__(self, parent=None):
        super().__init__(parent)
        u = current_user()
        self.show_cost = u.can("view_profit")
        self.can_adjust = u.can("stock_adjust")
        row = hbox()
        self.k_products = KpiCard("Products", "boxes", compact=True)
        self.k_qty = KpiCard("Units in stock", "layers", compact=True)
        self.k_value = KpiCard("Stock value (cost)", "indian-rupee", compact=True)
        self.k_retail = KpiCard("Retail value", "badge-percent", compact=True)
        self.k_low = KpiCard("Low stock", "triangle-alert", compact=True)
        self.k_out = KpiCard("Out of stock", "circle-alert", compact=True)
        for k in (self.k_products, self.k_qty, self.k_value, self.k_retail, self.k_low, self.k_out):
            if k in (self.k_value, self.k_retail) and not self.show_cost:
                continue
            row.addWidget(k)
        self.k_low.clicked.connect(lambda: self._filter("LOW"))
        self.k_out.clicked.connect(lambda: self._filter("OUT"))
        self.root.addLayout(row)
        self.tabs = QTabWidget()
        self.root.addWidget(self.tabs, 1)
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 10, 0, 0)
        card = Card()
        cols = [Col("code", "ID", width=70), Col("name", "Product", width=250, bold=True), Col("category", "Category", width=140),
                Col("quantity", "Stock", "int", 70, color=lambda r: "bad" if r["status"] == "OUT" else ("warn" if r["status"] == "LOW" else None)),
                Col("min_stock", "Min stock", "int", 80)]
        if self.show_cost:
            cols += [Col("avg_cost", "Avg cost", "money", 100), Col("stock_value", "Stock value", "money", 115)]
        cols += [Col("selling_price", "Selling price", "money", 105)]
        if self.show_cost:
            cols += [Col("retail_value", "Retail value", "money", 115)]
        cols += [Col("status", "Status", "status", 90)]
        self.table = DataTable(cols, placeholder="Search stock…", stretch="name")
        self.filter = QComboBox()
        for t, v in (("All stock", None), ("Low stock", "LOW"), ("Out of stock", "OUT"), ("Needs attention", "ALERT")):
            self.filter.addItem(t, v)
        self.group = QComboBox()
        for t, g in (("All groups", None), ("Mobile phones", "MOBILE"), ("Accessories", "ACCESSORY"), ("Spare parts", "SPARE")):
            self.group.addItem(t, g)
        self.table.add_top(self.group)
        self.table.add_top(self.filter)
        self.filter.currentIndexChanged.connect(lambda *_: self.refresh())
        self.group.currentIndexChanged.connect(lambda *_: self.refresh())
        self.table.activated.connect(self.show_history)
        card.add(self.table)
        btns = []
        if self.can_adjust:
            btns.append(button("Adjust stock", "primary", "sliders-horizontal", self.adjust))
        btns += [button("Movement history", "", "history", lambda: self.show_history(self.table.selected())),
                 button("Export", "", "file-spreadsheet", lambda: self.table.export(self, "Stock")), None]
        card.add(hbox(*btns))
        lay.addWidget(card)
        self.tabs.addTab(w, "Current stock")
        w2 = QWidget()
        l2 = QVBoxLayout(w2)
        l2.setContentsMargins(0, 10, 0, 0)
        c2 = Card("Stock movement history", "every stock change, never edited")
        self.mv_product = ProductPicker(placeholder="Filter by product (optional)…")
        self.mv_product.picked.connect(self._mv_pick)
        self.mv_pid = None
        self.mv_type = QComboBox()
        self.mv_type.addItem("All movement types", None)
        for k, v in MOVEMENT_LABELS.items():
            self.mv_type.addItem(v, k)
        self.mv_range = DateRangeBar("Last 30 days")
        self.mv_clear = button("All products", "ghost", None, self._mv_clear)
        c2.add(hbox(self.mv_product, self.mv_clear, self.mv_type, self.mv_range))
        self.mv_label = label("Showing all products", "Muted")
        c2.add(self.mv_label)
        self.mv_table = DataTable([Col("business_date", "Date", "date", 100), Col("created_at", "Recorded", "datetime", 150),
                                   Col("product", "Product", width=220, bold=True), Col("type_label", "Type", width=150),
                                   Col("reference", "Reference", width=140),
                                   Col("qty_in", "In", "int", 60, color=lambda r: "good" if r["qty_in"] else None),
                                   Col("qty_out", "Out", "int", 60, color=lambda r: "bad" if r["qty_out"] else None),
                                   Col("qty_after", "Balance", "int", 75, bold=True), Col("unit_cost", "Unit cost", "money", 95),
                                   Col("notes", "Notes", width=200)], search=True, stretch="notes")
        c2.add(self.mv_table)
        self.mv_type.currentIndexChanged.connect(lambda *_: self.refresh_moves())
        self.mv_range.changed.connect(lambda *_: self.refresh_moves())
        l2.addWidget(c2)
        self.tabs.addTab(w2, "Stock movements")
        self.tabs.currentChanged.connect(lambda *_: self.refresh())

    def _filter(self, f):
        self.tabs.setCurrentIndex(0)
        self.filter.setCurrentIndex(max(0, self.filter.findData(f)))

    def open_with(self, payload):
        if isinstance(payload, dict) and payload.get("filter"):
            self._filter(payload["filter"])

    def refresh(self):
        st = inventory.stock_summary()
        self.k_products.set(qty(st["total_products"]))
        self.k_qty.set(qty(st["total_quantity"]))
        self.k_value.set(inr(st["stock_value"]))
        self.k_retail.set(inr(st["retail_value"]))
        self.k_low.set(qty(st["low_stock"]), "", "warn" if st["low_stock"] else "")
        self.k_out.set(qty(st["out_of_stock"]), "", "bad" if st["out_of_stock"] else "")
        if self.tabs.currentIndex() == 0:
            rows = inventory.list_products(group=self.group.currentData(), stock_filter=self.filter.currentData())
            self.table.set_rows(rows)
            txt = f"{len(rows)} products · {qty(sum(max(r['quantity'], 0) for r in rows))} units"
            if self.show_cost:
                txt += f" · value {inr(sum((r['stock_value'] for r in rows), Decimal(0)))}"
            self.table.set_footer(txt)
        else:
            self.mv_product.reload()
            self.refresh_moves()

    def refresh_moves(self):
        a, b = self.mv_range.range()
        self.mv_table.set_rows(inventory.product_movements(self.mv_pid, a, b, self.mv_type.currentData()))

    def _mv_pick(self, p):
        self.mv_pid = p["id"]
        self.mv_label.setText(f"Showing: {p['name']} (current stock {p['quantity']})")
        self.refresh_moves()

    def _mv_clear(self):
        self.mv_pid = None
        self.mv_label.setText("Showing all products")
        self.refresh_moves()

    def show_history(self, row):
        if not row:
            error(self, "Select a product first.")
            return
        self.tabs.setCurrentIndex(1)
        self._mv_pick(row)

    def adjust(self):
        r = self.table.selected()
        if not r:
            error(self, "Select the product to adjust.")
            return
        if AdjustDialog(self, inventory.get_product(r["id"])).exec() == QDialog.Accepted:
            from ..bus import changed, toast
            changed("stock")
            toast("Stock adjusted and recorded in the audit log")
            self.refresh()
