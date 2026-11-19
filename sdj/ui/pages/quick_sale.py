"""Quick Sale: product → quantity → price → payment mode → Save. Everything else is automatic."""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QFormLayout, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout

from ...core import inventory, reports, sales
from ...core.auth import current_user
from ...db.base import D, q2
from ...output.fmt import inr, qty
from .. import icons
from ..bus import toast
from ..theme import current
from ..widgets import (Card, Col, DataTable, KpiCard, ModeButtons, MoneySpin, Page, ProductPicker, QtySpin,
                       add_shortcut, button, error, label, new_ref, run_safely)


class QuickSalePage(Page):
    subtitle = "The fastest way to record a counter sale"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.product = None
        self._ref = new_ref()
        top = QHBoxLayout()
        top.setSpacing(16)
        form_card = Card("Record a sale", "Enter saves")
        f = QFormLayout()
        f.setVerticalSpacing(12)
        f.setLabelAlignment(Qt.AlignRight)
        self.picker = ProductPicker(placeholder="1. Select product – type name / code or scan barcode")
        self.picker.setObjectName("BigInput")
        self.picker.picked.connect(self.set_product)
        self.chosen = label("No product selected", "Muted")
        self.imei = QComboBox()
        self.imei.setVisible(False)
        self.qty = QtySpin()
        self.qty.setObjectName("BigInput")
        self.price = MoneySpin()
        self.price.setObjectName("BigInput")
        self.mode = ModeButtons()
        self.total = label("₹0.00", "BigTotal")
        self.profit = label("", "Muted")
        f.addRow("Product", self.picker)
        f.addRow("", self.chosen)
        self.imei_row_label = QLabel("IMEI")
        f.addRow(self.imei_row_label, self.imei)
        self.imei_row_label.setVisible(False)
        f.addRow("2. Quantity", self.qty)
        f.addRow("3. Selling price", self.price)
        f.addRow("4. Payment", self.mode)
        f.addRow("Total", self.total)
        f.addRow("", self.profit)
        form_card.add(f)
        self.save_btn = button("5. Save sale", "primary", "save", self.save)
        self.save_btn.setMinimumHeight(44)
        form_card.add(self.save_btn)
        top.addWidget(form_card, 3)
        side = QVBoxLayout()
        side.setSpacing(12)
        self.k_count = KpiCard("Quick sales today", "zap")
        self.k_amount = KpiCard("Quick sales amount", "indian-rupee")
        self.k_all = KpiCard("All sales today", "receipt")
        for k in (self.k_count, self.k_amount, self.k_all):
            side.addWidget(k)
        side.addWidget(label("Stock, revenue, profit and all reports update the moment you save.", "Muted", wrap=True))
        side.addStretch(1)
        top.addLayout(side, 2)
        self.root.addLayout(top)
        lst = Card("Today's quick sales")
        self.table = DataTable([Col("created_at", "Time", "datetime", 150), Col("invoice_no", "Invoice", width=150),
                                Col("items_text", "Product", width=260), Col("grand_total", "Amount", "money", 110),
                                Col("mode", "Paid by", width=90), Col("status", "Status", "status", 100)],
                               search=False, stretch="items_text")
        self.table.activated.connect(self._open)
        lst.add(self.table)
        self.root.addWidget(lst, 1)
        self.qty.valueChanged.connect(self._update)
        self.price.valueChanged.connect(self._update)
        for w in (self.qty, self.price):
            w.lineEdit().returnPressed.connect(self.save)
        add_shortcut(self, "Ctrl+Return", self.save)

    def refresh(self):
        self.picker.reload()
        from ...core.business_day import current_business_date
        from ...db.base import session_scope
        with session_scope() as s:
            bdate = current_business_date(s)
        rows = []
        for r in sales.list_sales(start=bdate, end=bdate):
            if r["sale_type"] != "QUICK":
                continue
            d = sales.get_sale(r["id"])
            r["items_text"] = ", ".join(f"{l['product_name']} × {l['quantity']}" for l in d["lines"])
            r["mode"] = ", ".join(p["mode_label"] for p in d["payments"] if p["direction"] == "IN") or "—"
            rows.append(r)
        self.table.set_rows(rows)
        done = [r for r in rows if r["status"] == "COMPLETED"]
        self.k_count.set(qty(len(done)))
        self.k_amount.set(inr(sum((r["grand_total"] for r in done), D(0))))
        f = reports.period_figures(bdate, bdate)
        self.k_all.set(inr(f["f"]["net_counter_sales"]), f"{f['counts'].get('invoices', 0)} invoices (excl. GST)")
        self.picker.setFocus()

    def set_product(self, p: dict):
        imei_hint = p.get("_imei")
        p = inventory.get_product(p["id"])
        if p["quantity"] <= 0:
            error(self, f"'{p['name']}' is out of stock. Record a purchase or stock adjustment first.")
            return
        self.product = p
        pal = current()
        self.chosen.setText(f"<b>{p['name']}</b> · {p['category']} · in stock: <b>{p['quantity']}</b>"
                            f" · MRP/selling {inr(p['selling_price'])}")
        self.chosen.setStyleSheet(f"color: {pal.text2};")
        self.price.set_d(p["selling_price"])
        self.qty.setMaximum(max(1, p["quantity"]))
        self.qty.setValue(1)
        is_imei = p["track_imei"]
        self.imei.setVisible(is_imei)
        self.imei_row_label.setVisible(is_imei)
        self.qty.setEnabled(not is_imei)
        if is_imei:
            self.imei.clear()
            for r in inventory.available_imeis(p["id"]):
                self.imei.addItem(r["label"], r["id"])
            if imei_hint:
                self.imei.setCurrentIndex(max(0, self.imei.findData(imei_hint["id"])))
        self._update()
        self.qty.setFocus()

    def _update(self):
        if not self.product:
            self.total.setText("₹0.00")
            self.profit.setText("")
            return
        total = q2(self.price.value_d() * self.qty.value())
        self.total.setText(inr(total))
        if current_user().can("view_profit"):
            cost = q2(D(self.product["avg_cost"]) * self.qty.value())
            gp = total - cost
            pal = current()
            self.profit.setText(f"Estimated gross profit {inr(gp)} (before GST)")
            self.profit.setStyleSheet(f"color: {pal.good if gp >= 0 else pal.bad};")

    def save(self):
        if not self.product:
            error(self, "Select a product first.")
            self.picker.setFocus()
            return
        imei_id = self.imei.currentData() if self.product["track_imei"] else None
        if self.product["track_imei"] and not imei_id:
            error(self, "Select the IMEI of the phone being sold.")
            return
        self.save_btn.setEnabled(False)
        try:
            res = run_safely(self, sales.quick_sale, self.product["id"], self.qty.value(), self.price.value_d(),
                             self.mode.value(), client_ref=self._ref, imei_record_id=imei_id, topic="sales")
        finally:
            self.save_btn.setEnabled(True)
        if not res:
            return
        toast(f"Saved {res['invoice_no']} · {inr(res['grand_total'])} · stock updated")
        self._ref = new_ref()
        self.product = None
        self.chosen.setText("No product selected")
        self.imei.setVisible(False)
        self.imei_row_label.setVisible(False)
        self.qty.setEnabled(True)
        self.qty.setValue(1)
        self.price.setValue(0)
        self._update()
        self.picker.setFocus()

    def _open(self, row):
        from ..dialogs import SaleDetailDialog
        SaleDetailDialog(self, row["id"]).exec()
