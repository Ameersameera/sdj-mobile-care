"""Purchases: stock-in from suppliers (landed cost, GST, IMEIs), payments and history."""
from __future__ import annotations

from decimal import Decimal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from ...core import inventory, parties, purchases, settings
from ...db.base import D, q2
from ...output.fmt import d_only, inr
from .. import icons
from ..bus import changed, toast
from ..dialogs import ImeiEntryDialog, PaymentDialog, ProductDialog, SupplierDialog
from ..theme import current
from ..widgets import (Card, Col, DataTable, DateEdit, DateRangeBar, ModeButtons, MoneySpin, Page, ProductPicker,
                       QtySpin, ask_text, button, confirm, error, hbox, label, new_ref, run_safely)

GST_RATES = ["0", "5", "12", "18", "28"]


class PurchasesPage(Page):
    subtitle = "Purchases increase stock and update the weighted-average cost automatically"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.lines: list[dict] = []
        self._ref = new_ref()
        self.tabs = QTabWidget()
        self.root.addWidget(self.tabs, 1)
        # ---------------- new purchase
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 10, 0, 0)
        lay.setSpacing(12)
        head = Card("Supplier & invoice")
        g = QGridLayout()
        g.setHorizontalSpacing(12)
        self.supplier = QComboBox()
        self.supplier.setMinimumWidth(240)
        self.inv_no = QLineEdit()
        self.inv_no.setPlaceholderText("Supplier's bill number")
        self.pdate = DateEdit()
        g.addWidget(QLabel("Supplier *"), 0, 0)
        g.addWidget(self.supplier, 0, 1)
        g.addWidget(button("New supplier", "ghost", "user-plus", self.new_supplier), 0, 2)
        g.addWidget(QLabel("Invoice no."), 0, 3)
        g.addWidget(self.inv_no, 0, 4)
        g.addWidget(QLabel("Purchase date"), 0, 5)
        g.addWidget(self.pdate, 0, 6)
        head.add(g)
        lay.addWidget(head)
        items = Card("Items received", "prices excluding GST")
        self.picker = ProductPicker(placeholder="Search product to add (or scan barcode)…")
        self.picker.picked.connect(self.add_line)
        items.add(hbox(self.picker, button("New product", "", "package-plus", self.new_product)))
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Product", "Qty", "Unit price", "GST %", "IMEIs", "Line total", ""])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for i, wd in ((1, 80), (2, 120), (3, 80), (4, 150), (5, 120), (6, 44)):
            self.table.setColumnWidth(i, wd)
            hh.setSectionResizeMode(i, QHeaderView.Fixed)
        items.add(self.table)
        lay.addWidget(items, 1)
        foot = Card()
        fg = QGridLayout()
        fg.setHorizontalSpacing(14)
        self.subtotal_lbl = QLabel("₹0.00")
        self.gst_lbl = QLabel("₹0.00")
        self.discount = MoneySpin()
        self.charges = MoneySpin()
        self.total_lbl = label("₹0.00", "BigTotal")
        self.paid = MoneySpin()
        self.mode = ModeButtons()
        self.balance_lbl = label("", "")
        self.notes = QLineEdit()
        self.notes.setPlaceholderText("Notes (optional)")
        fg.addWidget(QLabel("Sub total"), 0, 0)
        fg.addWidget(self.subtotal_lbl, 0, 1)
        fg.addWidget(QLabel("GST"), 1, 0)
        fg.addWidget(self.gst_lbl, 1, 1)
        fg.addWidget(QLabel("Less: discount"), 2, 0)
        fg.addWidget(self.discount, 2, 1)
        fg.addWidget(QLabel("Add: freight / other charges"), 3, 0)
        fg.addWidget(self.charges, 3, 1)
        fg.addWidget(label("PURCHASE TOTAL", "BigTotalLabel"), 0, 2)
        fg.addWidget(self.total_lbl, 1, 2, 2, 1)
        fg.addWidget(QLabel("Amount paid now"), 0, 3)
        fg.addWidget(self.paid, 0, 4)
        fg.addWidget(QLabel("Mode"), 1, 3)
        fg.addWidget(self.mode, 1, 4)
        fg.addWidget(self.balance_lbl, 2, 3, 1, 2)
        fg.addWidget(self.notes, 3, 2, 1, 3)
        foot.add(fg)
        self.save_btn = button("Save purchase & add stock", "primary", "save", self.save)
        foot.add(hbox(label("GST you pay is added to cost unless GST is switched on in Settings (then it is input credit).",
                            "Muted"), None, button("Clear", "ghost", "trash-2", self.clear), self.save_btn))
        lay.addWidget(foot)
        for wdg in (self.discount, self.charges, self.paid):
            wdg.valueChanged.connect(self.recalc)
        self.tabs.addTab(w, "New purchase")
        # ---------------- history
        w2 = QWidget()
        l2 = QVBoxLayout(w2)
        l2.setContentsMargins(0, 10, 0, 0)
        c2 = Card()
        self.range = DateRangeBar("This month")
        self.range.changed.connect(lambda *_: self.refresh_history())
        self.hist = DataTable([Col("business_date", "Date", "date", 100), Col("purchase_no", "Purchase no", width=110, bold=True),
                               Col("supplier", "Supplier", width=200), Col("invoice_no", "Supplier inv.", width=120),
                               Col("items", "Items", "int", 60), Col("total", "Total", "money", 110),
                               Col("paid", "Paid", "money", 110), Col("balance", "Due", "money", 100,
                                                                      color=lambda r: "warn" if r["balance"] > 0 else None),
                               Col("status", "Status", "status", 100)], placeholder="Search purchases…", stretch="supplier")
        self.hist.add_top(self.range)
        self.hist.activated.connect(self.view)
        c2.add(self.hist)
        c2.add(hbox(button("View", "primary", "eye", lambda: self.view(self.hist.selected())),
                    button("Pay supplier", "", "hand-coins", self.pay),
                    button("Cancel purchase", "danger", "x", self.cancel),
                    button("Export", "", "file-spreadsheet", lambda: self.hist.export(self, "Purchases")), None))
        l2.addWidget(c2)
        self.tabs.addTab(w2, "Purchase history")
        self.tabs.currentChanged.connect(lambda *_: self.refresh())
        self.render()

    # ------------------------------------------------------------ data
    def refresh(self):
        cur = self.supplier.currentData()
        self.supplier.clear()
        self.supplier.addItem("— select supplier —", None)
        for s in inventory.suppliers_for_combo():
            self.supplier.addItem(s["name"], s["id"])
        if cur:
            self.supplier.setCurrentIndex(max(0, self.supplier.findData(cur)))
        self.picker.reload()
        if self.tabs.currentIndex() == 1:
            self.refresh_history()

    def refresh_history(self):
        a, b = self.range.range()
        rows = purchases.list_purchases(a, b)
        self.hist.set_rows(rows)
        act = [r for r in rows if r["status"] == "ACTIVE"]
        self.hist.set_footer(f"{len(act)} purchases · {inr(sum((r['total'] for r in act), Decimal(0)))} · due "
                             f"{inr(sum((r['balance'] for r in act), Decimal(0)))}")

    def new_supplier(self):
        dlg = SupplierDialog(self)
        if dlg.exec() == QDialog.Accepted:
            self.refresh()
            self.supplier.setCurrentIndex(max(0, self.supplier.findData(dlg.result_id)))

    def new_product(self):
        dlg = ProductDialog(self)
        if dlg.exec() == QDialog.Accepted:
            self.picker.reload()
            self.add_line(inventory.get_product(dlg.result_id))

    def add_line(self, p: dict):
        p = inventory.get_product(p["id"])
        for l in self.lines:
            if l["product"]["id"] == p["id"]:
                l["qty"] += 1
                self.render()
                return
        gst = str(int(p["gst_rate"])) if str(int(p["gst_rate"])) in GST_RATES else "18"
        self.lines.append(dict(product=p, qty=1, price=D(p["purchase_price"]), gst=gst, imeis=[]))
        self.render()

    def render(self):
        self.table.setRowCount(len(self.lines))
        pal = current()
        for r, l in enumerate(self.lines):
            p = l["product"]
            self.table.setCellWidget(r, 0, QLabel(f"  <b>{p['name']}</b> <span style='color:{pal.muted}'>· stock {p['quantity']}</span>"))
            q = QtySpin()
            q.setValue(l["qty"])
            q.valueChanged.connect(lambda v, i=r: self._set(i, "qty", v))
            self.table.setCellWidget(r, 1, q)
            pr = MoneySpin()
            pr.set_d(l["price"])
            pr.valueChanged.connect(lambda v, i=r, wd=pr: self._set(i, "price", wd.value_d()))
            self.table.setCellWidget(r, 2, pr)
            gst = QComboBox()
            gst.addItems(GST_RATES)
            gst.setCurrentText(l["gst"])
            gst.currentTextChanged.connect(lambda t, i=r: self._set(i, "gst", t))
            self.table.setCellWidget(r, 3, gst)
            if p["track_imei"]:
                b = QPushButton(f"{len(l['imeis'])}/{l['qty']} entered")
                b.setIcon(icons.icon("smartphone", pal.good if len(l["imeis"]) == l["qty"] else pal.warn, 14))
                b.clicked.connect(lambda _=False, i=r: self._imeis(i))
                self.table.setCellWidget(r, 4, b)
            else:
                self.table.setCellWidget(r, 4, QLabel("  not needed"))
            it = QTableWidgetItem("")
            it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(r, 5, it)
            rm = QPushButton()
            rm.setFlat(True)
            rm.setIcon(icons.icon("x", pal.bad, 16))
            rm.clicked.connect(lambda _=False, i=r: (self.lines.pop(i), self.render()))
            self.table.setCellWidget(r, 6, rm)
        self.recalc()

    def _set(self, i, key, value):
        if i < len(self.lines):
            self.lines[i][key] = value
            if key == "qty" and self.lines[i]["product"]["track_imei"]:
                self.render()
                return
            self.recalc()

    def _imeis(self, i):
        l = self.lines[i]
        existing = "\n".join(", ".join(x for x in (u["imei1"], u.get("imei2", ""), u.get("serial_no", "")) if x) for u in l["imeis"])
        dlg = ImeiEntryDialog(self, l["qty"], f"IMEIs – {l['product']['name']}", existing)
        if dlg.exec() == QDialog.Accepted:
            l["imeis"] = dlg.units
            self.render()

    def _calc(self):
        lines = [dict(quantity=l["qty"], unit_price=l["price"], gst_rate=D(l["gst"])) for l in self.lines]
        return purchases.compute_purchase(lines, self.discount.value_d(), self.charges.value_d())

    def recalc(self):
        c = self._calc()
        for r, tot in enumerate(c["line_totals"]):
            it = self.table.item(r, 5)
            if it:
                it.setText(inr(tot))
        self.subtotal_lbl.setText(inr(c["subtotal"]))
        self.gst_lbl.setText(inr(c["gst_total"]))
        self.total_lbl.setText(inr(c["total"]))
        bal = c["total"] - self.paid.value_d()
        pal = current()
        self.balance_lbl.setText(f"<b style='color:{pal.warn if bal > 0 else pal.good}'>"
                                 f"{'Due to supplier: ' + inr(bal) if bal > 0 else ('Fully paid' if c['total'] > 0 else '')}</b>")

    def clear(self):
        self.lines = []
        self._ref = new_ref()
        self.inv_no.clear()
        self.discount.setValue(0)
        self.charges.setValue(0)
        self.paid.setValue(0)
        self.notes.clear()
        self.render()

    def save(self):
        sid = self.supplier.currentData()
        if not sid:
            error(self, "Select the supplier.")
            return
        if not self.lines:
            error(self, "Add at least one product.")
            return
        for l in self.lines:
            if l["product"]["track_imei"] and len(l["imeis"]) != l["qty"]:
                error(self, f"Enter the IMEI numbers for '{l['product']['name']}' ({len(l['imeis'])}/{l['qty']}).")
                return
        lines = [dict(product_id=l["product"]["id"], quantity=l["qty"], unit_price=l["price"], gst_rate=D(l["gst"]),
                      imei_units=l["imeis"]) for l in self.lines]
        self.save_btn.setEnabled(False)
        try:
            res = run_safely(self, purchases.create_purchase, sid, lines, supplier_invoice_no=self.inv_no.text(),
                             purchase_date=self.pdate.value(), discount=self.discount.value_d(),
                             additional_charges=self.charges.value_d(), paid_amount=self.paid.value_d(),
                             payment_mode=self.mode.value(), notes=self.notes.text(), client_ref=self._ref, topic="purchases")
        finally:
            self.save_btn.setEnabled(True)
        if res:
            toast(f"Purchase {res['purchase_no']} saved · {inr(res['total'])} · stock added")
            self.clear()

    # ------------------------------------------------------------ history actions
    def view(self, row):
        if not row:
            return
        d = purchases.get_purchase(row["id"])
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Purchase {d['purchase_no']}")
        dlg.resize(820, 460)
        lay = QVBoxLayout(dlg)
        lay.addWidget(label(f"{d['purchase_no']} · {d['supplier']} · {d['invoice_no'] or 'no invoice no.'}", "SectionTitle"))
        lay.addWidget(label(f"Purchase date {d_only(d['purchase_date'])} · total {inr(d['total'])} · paid {inr(d['paid'])} · "
                            f"due {inr(d['balance'] if d['status'] == 'ACTIVE' else 0)} · {d['status'].title()}"
                            + (f" · cancelled: {d['cancel_reason']}" if d["cancel_reason"] else ""), "Muted", wrap=True))
        t = DataTable([Col("product", "Product", width=220), Col("quantity", "Qty", "int", 60),
                       Col("unit_price", "Unit price", "money", 100), Col("gst_rate", "GST %", "money", 70),
                       Col("total", "Line total", "money", 110), Col("landed", "Landed cost/unit", "money", 120),
                       Col("imeis", "IMEIs", width=200)], search=False, stretch="product")
        t.set_rows(d["lines"])
        lay.addWidget(t)
        dlg.exec()

    def pay(self):
        r = self.hist.selected()
        if not r or r["balance"] <= 0:
            error(self, "Select a purchase that has a balance due.")
            return
        dlg = PaymentDialog(self, f"Pay supplier – {r['supplier']}", r["balance"], "Amount paid")
        if dlg.exec() == QDialog.Accepted:
            amt, mode, note = dlg.result
            run_safely(self, parties.pay_supplier, r["supplier_id"], amt, mode, note, purchase_id=r["id"],
                       client_ref=new_ref(), success=f"Paid {inr(amt)} to {r['supplier']}", topic="purchases")
            self.refresh_history()

    def cancel(self):
        r = self.hist.selected()
        if not r:
            return
        reason = ask_text(self, "Cancel purchase", f"Why is {r['purchase_no']} being cancelled?\nThe stock will be removed again.")
        if reason:
            run_safely(self, purchases.cancel_purchase, r["id"], reason, success="Purchase cancelled", topic="purchases")
            self.refresh_history()
