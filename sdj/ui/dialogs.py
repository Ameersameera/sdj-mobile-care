"""Shared dialogs: customers, suppliers, products, IMEI entry/selection, payments, invoice view, cash count."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QSpinBox, QTabWidget,
                               QVBoxLayout, QWidget)

from ..core import inventory, parties, sales, settings
from ..core.errors import BusinessError, ValidationError
from ..db.base import D, q2
from ..output.fmt import d_only, dt, inr
from . import icons
from .bus import changed, toast
from .theme import current
from .widgets import (Card, Col, DataTable, DateEdit, FormDialog, ModeButtons, MoneySpin, QtySpin, button, confirm,
                      error, hbox, label, multiline, new_ref, run_safely)

GROUP_GST_KEY = {"MOBILE": "default_gst_mobile", "ACCESSORY": "default_gst_accessory", "SPARE": "default_gst_spare"}


# ================================================================ customers / suppliers
class CustomerDialog(FormDialog):
    def __init__(self, parent, preset: dict | None = None, customer_id: int | None = None):
        super().__init__(parent, "Edit customer" if customer_id else "New customer", 460)
        self.customer_id = customer_id
        d = parties.get_customer(customer_id) if customer_id else (preset or {})
        self.name = QLineEdit(d.get("name", ""))
        self.phone = QLineEdit(d.get("phone", ""))
        self.phone.setPlaceholderText("10-digit mobile number")
        self.address = multiline("Address (optional)", 56)
        self.address.setPlainText(d.get("address", ""))
        self.email = QLineEdit(d.get("email", ""))
        self.gstin = QLineEdit(d.get("gstin", ""))
        self.gstin.setPlaceholderText("Only for business customers")
        self.opening = MoneySpin()
        self.opening.set_d(d.get("opening_balance", 0))
        self.notes = QLineEdit(d.get("notes", ""))
        self.form.addRow("Name *", self.name)
        self.form.addRow("Phone", self.phone)
        self.form.addRow("Address", self.address)
        self.form.addRow("Email", self.email)
        self.form.addRow("GSTIN", self.gstin)
        self.form.addRow("Old dues (opening)", self.opening)
        self.form.addRow("Notes", self.notes)
        self.finish_layout()
        (self.phone if d.get("name") and not d.get("phone") else self.name).setFocus()

    def on_save(self):
        data = dict(name=self.name.text(), phone=self.phone.text(), address=self.address.toPlainText(),
                    email=self.email.text(), gstin=self.gstin.text(), opening_balance=self.opening.value_d(),
                    notes=self.notes.text())
        if self.customer_id:
            parties.update_customer(self.customer_id, data)
            self.result_id = self.customer_id
        else:
            self.result_id = parties.create_customer(data)
        changed("customers")
        toast("Customer saved")


class SupplierDialog(FormDialog):
    def __init__(self, parent, supplier: dict | None = None):
        super().__init__(parent, "Edit supplier" if supplier else "New supplier", 460)
        self.supplier = supplier
        d = supplier or {}
        self.name = QLineEdit(d.get("name", ""))
        self.contact = QLineEdit(d.get("contact_person", ""))
        self.phone = QLineEdit(d.get("phone", ""))
        self.email = QLineEdit(d.get("email", ""))
        self.address = multiline("Address", 56)
        self.address.setPlainText(d.get("address", ""))
        self.gstin = QLineEdit(d.get("gstin", ""))
        self.opening = MoneySpin()
        self.opening.set_d(d.get("opening_balance", 0))
        self.notes = QLineEdit(d.get("notes", ""))
        for lbl, w in (("Name *", self.name), ("Contact person", self.contact), ("Phone", self.phone),
                       ("Email", self.email), ("Address", self.address), ("GSTIN", self.gstin),
                       ("Amount we owe (opening)", self.opening), ("Notes", self.notes)):
            self.form.addRow(lbl, w)
        self.finish_layout()

    def on_save(self):
        data = dict(name=self.name.text(), contact_person=self.contact.text(), phone=self.phone.text(),
                    email=self.email.text(), address=self.address.toPlainText(), gstin=self.gstin.text(),
                    opening_balance=self.opening.value_d(), notes=self.notes.text())
        if self.supplier:
            parties.update_supplier(self.supplier["id"], data)
            self.result_id = self.supplier["id"]
        else:
            self.result_id = parties.create_supplier(data)
        changed("suppliers")
        toast("Supplier saved")


# ================================================================ IMEI dialogs
class ImeiEntryDialog(QDialog):
    """Enter N IMEIs – one per line: IMEI1[, IMEI2][, Serial]. Works with barcode scanners."""

    def __init__(self, parent, count: int, title: str = "Enter IMEI / serial numbers", existing: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(520, 420)
        self.count = count
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.addWidget(label(f"Enter {count} IMEI number(s), one phone per line.", "SectionTitle"))
        lay.addWidget(label("Format: IMEI 1, IMEI 2, Serial (IMEI 2 and serial are optional). "
                            "Scanning with a barcode scanner adds a new line automatically.", "Muted", wrap=True))
        self.text = QPlainTextEdit(existing)
        self.text.setPlaceholderText("356938035643809\n356938035643817, 356938035643825")
        lay.addWidget(self.text, 1)
        self.status = label("", "Muted")
        lay.addWidget(self.status)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setProperty("variant", "primary")
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.text.textChanged.connect(self._update)
        self.units: list[dict] = []
        self._update()

    def _update(self):
        n = len(inventory.parse_imei_lines(self.text.toPlainText()))
        p = current()
        color = p.good if n == self.count else p.warn
        self.status.setText(f"{n} of {self.count} entered")
        self.status.setStyleSheet(f"color: {color}; font-weight: 600;")

    def _ok(self):
        units = inventory.parse_imei_lines(self.text.toPlainText())
        try:
            if len(units) != self.count:
                raise ValidationError(f"Please enter exactly {self.count} IMEI number(s); {len(units)} entered.")
            for u in units:
                inventory.validate_imei(u["imei1"])
                if u["imei2"]:
                    inventory.validate_imei(u["imei2"], "IMEI 2")
        except BusinessError as exc:
            error(self, str(exc))
            return
        self.units = units
        self.accept()


class ImeiSelectDialog(QDialog):
    def __init__(self, parent, product: dict, count: int = 1, exclude_ids: set[int] | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"Select IMEI – {product['name']}")
        self.setMinimumSize(480, 420)
        self.count = count
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.addWidget(label(f"Choose {count} phone(s) from stock", "SectionTitle"))
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Type or scan IMEI to filter…")
        lay.addWidget(self.filter)
        self.list = QListWidget()
        self.list.setObjectName("PlainList")
        for r in inventory.available_imeis(product["id"]):
            if exclude_ids and r["id"] in exclude_ids:
                continue
            it = QListWidgetItem(r["label"])
            it.setData(Qt.UserRole, r)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Unchecked)
            self.list.addItem(it)
        lay.addWidget(self.list, 1)
        self.filter.textChanged.connect(self._filter)
        self.filter.returnPressed.connect(self._scan)
        self.list.itemDoubleClicked.connect(lambda it: (it.setCheckState(Qt.Checked), self._ok() if self.count == 1 else None))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setProperty("variant", "primary")
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.selected: list[dict] = []
        if self.list.count() == 0:
            lay.insertWidget(1, label("No phones of this model are in stock.", "BadText"))

    def _filter(self, t: str):
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(t.strip().lower() not in it.text().lower())

    def _scan(self):
        visible = [self.list.item(i) for i in range(self.list.count()) if not self.list.item(i).isHidden()]
        if len(visible) == 1:
            visible[0].setCheckState(Qt.Checked)
            self.filter.clear()
            if self.count == 1:
                self._ok()

    def preselect(self, record_id: int):
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.data(Qt.UserRole)["id"] == record_id:
                it.setCheckState(Qt.Checked)

    def _ok(self):
        sel = [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
               if self.list.item(i).checkState() == Qt.Checked]
        if len(sel) != self.count:
            error(self, f"Please tick exactly {self.count} phone(s).")
            return
        self.selected = sel
        self.accept()


# ================================================================ products
class ProductDialog(FormDialog):
    def __init__(self, parent, product_id: int | None = None, group: str | None = None, name: str = ""):
        super().__init__(parent, "Edit product" if product_id else "Add product", 620)
        self.product_id = product_id
        p = inventory.get_product(product_id) if product_id else {}
        self.cats = inventory.list_categories()
        if group:
            self.cats = [c for c in self.cats if c["group"] == group] + [c for c in self.cats if c["group"] != group]
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(9)
        self.form.addRow(grid)
        self.code = QLineEdit(p.get("code", "Auto-generated"))
        self.code.setEnabled(False)
        self.name = QLineEdit(p.get("name", name))
        self.brand = QLineEdit(p.get("brand", ""))
        self.category = QComboBox()
        for c in self.cats:
            self.category.addItem(f"{c['name']}  ({c['group_label']})", c)
        if p:
            for i in range(self.category.count()):
                if self.category.itemData(i)["id"] == p["category_id"]:
                    self.category.setCurrentIndex(i)
        self.model = QLineEdit(p.get("model_number", ""))
        self.barcode = QLineEdit(p.get("barcode", ""))
        self.barcode.setPlaceholderText("Scan or type (optional)")
        self.purchase = MoneySpin()
        self.purchase.set_d(p.get("purchase_price", 0))
        self.selling = MoneySpin()
        self.selling.set_d(p.get("selling_price", 0))
        self.min_stock = QSpinBox()
        self.min_stock.setRange(0, 99999)
        self.min_stock.setValue(p.get("min_stock", int(settings.get("low_stock_default") or 0)))
        self.warranty = QSpinBox()
        self.warranty.setRange(0, 120)
        self.warranty.setSuffix(" months")
        self.warranty.setValue(p.get("warranty_months", 0))
        self.gst = QComboBox()
        for r in ("0", "5", "12", "18", "28"):
            self.gst.addItem(f"{r}%", r)
        self.hsn = QLineEdit(p.get("hsn_code", ""))
        self.supplier = QComboBox()
        self.supplier.addItem("—", None)
        for s in inventory.suppliers_for_combo():
            self.supplier.addItem(s["name"], s["id"])
        if p.get("supplier_id"):
            i = self.supplier.findData(p["supplier_id"])
            self.supplier.setCurrentIndex(max(i, 0))
        self.purchase_date = DateEdit(p.get("purchase_date") or date.today())
        self.track = QCheckBox("Track IMEI / serial number for every unit")
        self.track.setChecked(p.get("track_imei", False))
        self.desc = multiline("Description (optional)", 52)
        self.desc.setPlainText(p.get("description", ""))
        rows = [("Product ID", self.code, "Brand", self.brand), ("Product name *", self.name, "Category *", self.category),
                ("Model number", self.model, "Barcode", self.barcode),
                ("Purchase price", self.purchase, "Selling price *", self.selling),
                ("Minimum stock", self.min_stock, "Warranty", self.warranty),
                ("GST rate", self.gst, "HSN code", self.hsn), ("Supplier", self.supplier, "Purchase date", self.purchase_date)]
        for r, (l1, w1, l2, w2) in enumerate(rows):
            grid.addWidget(QLabel(l1), r, 0, Qt.AlignRight)
            grid.addWidget(w1, r, 1)
            grid.addWidget(QLabel(l2), r, 2, Qt.AlignRight)
            grid.addWidget(w2, r, 3)
        grid.addWidget(self.track, len(rows), 1, 1, 3)
        grid.addWidget(QLabel("Description"), len(rows) + 1, 0, Qt.AlignRight | Qt.AlignTop)
        grid.addWidget(self.desc, len(rows) + 1, 1, 1, 3)
        self.units: list[dict] = []
        if not product_id:
            box = Card("Opening stock", "Stock already in the shop today")
            self.opening = QtySpin(minimum=0)
            self.opening.setValue(0)
            self.imei_btn = button("Enter IMEIs…", "", "smartphone", self._enter_imeis)
            self.imei_info = label("", "Muted")
            box.add(hbox(QLabel("Quantity"), self.opening, 12, self.imei_btn, self.imei_info, None))
            self.root.addWidget(box)
            self.opening.valueChanged.connect(self._sync)
            self.track.toggled.connect(self._sync)
        self.category.currentIndexChanged.connect(self._cat_changed)
        self.margin = label("", "Muted")
        self.root.addWidget(self.margin)
        self.purchase.valueChanged.connect(self._show_margin)
        self.selling.valueChanged.connect(self._show_margin)
        if p:
            gi = self.gst.findData(str(int(p["gst_rate"])))
            self.gst.setCurrentIndex(gi if gi >= 0 else 3)
        else:
            self._cat_changed()
        self._show_margin()
        if not product_id:
            self._sync()
        self.finish_layout()
        self.name.setFocus()

    def _cat_changed(self):
        c = self.category.currentData()
        if not c:
            return
        if not self.product_id:
            self.track.setChecked(c["requires_imei"])
            rate = str(int(D(settings.get(GROUP_GST_KEY[c["group"]]) or 18)))
            i = self.gst.findData(rate)
            self.gst.setCurrentIndex(i if i >= 0 else 3)

    def _sync(self):
        if self.product_id:
            return
        need = self.track.isChecked() and self.opening.value() > 0
        self.imei_btn.setVisible(need)
        self.imei_info.setVisible(need)
        self.imei_info.setText(f"{len(self.units)} of {self.opening.value()} IMEI entered")

    def _enter_imeis(self):
        existing = "\n".join(", ".join(x for x in (u["imei1"], u["imei2"], u["serial_no"]) if x) for u in self.units)
        dlg = ImeiEntryDialog(self, self.opening.value(), existing=existing)
        if dlg.exec() == QDialog.Accepted:
            self.units = dlg.units
            self._sync()

    def _show_margin(self):
        pp, sp = self.purchase.value_d(), self.selling.value_d()
        if sp > 0 and pp > 0:
            m = sp - pp
            pct = (m / sp * 100).quantize(Decimal("0.1"))
            p = current()
            self.margin.setText(f"Margin per unit: {inr(m)} ({pct}% of selling price)")
            self.margin.setStyleSheet(f"color: {p.good if m >= 0 else p.bad};")
        else:
            self.margin.setText("")

    def on_save(self):
        c = self.category.currentData()
        data = dict(name=self.name.text(), brand=self.brand.text(), category_id=c["id"] if c else None,
                    model_number=self.model.text(), barcode=self.barcode.text(), purchase_price=self.purchase.value_d(),
                    selling_price=self.selling.value_d(), min_stock=self.min_stock.value(),
                    warranty_months=self.warranty.value(), gst_rate=D(self.gst.currentData()), hsn_code=self.hsn.text(),
                    supplier_id=self.supplier.currentData(), purchase_date=self.purchase_date.value(),
                    description=self.desc.toPlainText(), track_imei=self.track.isChecked())
        if data["selling_price"] <= 0:
            raise ValidationError("Enter the selling price.")
        if data["purchase_price"] > data["selling_price"] and not confirm(
                self, "Selling below cost?", "The selling price is lower than the purchase price. Save anyway?"):
            return False
        if self.product_id:
            inventory.update_product(self.product_id, data)
            self.result_id = self.product_id
        else:
            qty = self.opening.value()
            if data["track_imei"] and qty and len(self.units) != qty:
                self._enter_imeis()
                if len(self.units) != qty:
                    raise ValidationError("Please enter the IMEI numbers for the opening stock.")
            self.result_id = inventory.create_product(data, opening_qty=qty, opening_cost=data["purchase_price"],
                                                      imei_units=self.units)
        changed("stock")
        toast(f"Product '{data['name']}' saved")


# ================================================================ payment
class PaymentDialog(FormDialog):
    def __init__(self, parent, title: str, due, prompt: str = "Amount received", refund: bool = False):
        super().__init__(parent, title, 420)
        self.due = q2(due)
        self.form.addRow(label(f"Pending balance: {inr(self.due)}", "SectionTitle"))
        self.amount = MoneySpin()
        self.amount.set_d(self.due)
        self.modes = ModeButtons()
        self.notes = QLineEdit()
        self.form.addRow(prompt, self.amount)
        self.form.addRow("Mode", self.modes)
        self.form.addRow("Note", self.notes)
        self.ok_btn.setText("Record refund" if refund else "Record payment")
        self.finish_layout()
        self.amount.setFocus()

    def on_save(self):
        if self.amount.value_d() <= 0:
            raise ValidationError("Enter an amount.")
        if self.amount.value_d() > self.due:
            raise ValidationError(f"Amount cannot be more than {inr(self.due)}.")
        self.result = (self.amount.value_d(), self.modes.value(), self.notes.text())


# ================================================================ invoice view
class SaleDetailDialog(QDialog):
    def __init__(self, parent, sale_id: int):
        super().__init__(parent)
        self.sale_id = sale_id
        self.setMinimumSize(820, 620)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)
        self.head = label("", "SectionTitle")
        self.sub = label("", "Muted", wrap=True)
        lay.addWidget(self.head)
        lay.addWidget(self.sub)
        self.lines = DataTable([Col("product_name", "Item", width=240), Col("imei", "IMEI / SN", width=150),
                                Col("quantity", "Qty", "int", 50), Col("unit_price", "Rate", "money", 90),
                                Col("discount", "Discount", "money", 85), Col("gst", "GST", "money", 75),
                                Col("total", "Amount", "money", 100), Col("returned_qty", "Returned", "int", 70)],
                               search=False, stretch="product_name")
        lay.addWidget(self.lines, 2)
        self.pays = DataTable([Col("created_at", "Time", "datetime", 150), Col("mode_label", "Mode", width=110),
                               Col("kind", "Type", width=150), Col("direction", "In/Out", width=60),
                               Col("amount", "Amount", "money", 100), Col("status", "Status", "status", 80)],
                              search=False)
        lay.addWidget(label("Payments", "SectionTitle"))
        lay.addWidget(self.pays, 1)
        self.totals = label("", "")
        lay.addWidget(self.totals)
        bar = QHBoxLayout()
        bar.addWidget(button("A4 invoice", "", "file-text", lambda: self._print("A4")))
        bar.addWidget(button("Thermal 80mm", "", "printer", lambda: self._print("THERMAL_80")))
        bar.addWidget(button("Thermal 58mm", "", "printer", lambda: self._print("THERMAL_58")))
        bar.addStretch(1)
        self.collect_btn = button("Collect payment", "primary", "hand-coins", self._collect)
        self.return_btn = button("Return / exchange", "", "arrow-left-right", self._return)
        self.cancel_btn = button("Cancel bill", "danger", "x", self._cancel)
        bar.addWidget(self.collect_btn)
        bar.addWidget(self.return_btn)
        bar.addWidget(self.cancel_btn)
        bar.addWidget(button("Close", "", None, self.accept))
        lay.addLayout(bar)
        self.load()

    def load(self):
        from ..core.auth import current_user
        d = sales.get_sale(self.sale_id)
        self.d = d
        self.setWindowTitle(f"Invoice {d['invoice_no']}")
        st = "CANCELLED" if d["status"] == "CANCELLED" else ("Paid" if d["balance"] <= 0 else "Balance due")
        self.head.setText(f"{d['invoice_no']}  ·  {inr(d['grand_total'])}  ·  {st}")
        self.sub.setText(f"{dt(d['created_at'])}  ·  Business date {d_only(d['business_date'])}  ·  Customer: "
                         f"{d['customer_name']} {d['customer_phone']}" +
                         (f"\nCancelled: {d['cancel_reason']}" if d["status"] == "CANCELLED" else "") +
                         (f"\nNotes: {d['notes']}" if d.get("notes") else ""))
        self.lines.set_rows(d["lines"])
        self.pays.set_rows(d["payments"])
        self.totals.setText(f"Sub total {inr(d['gross_amount'])}  ·  Discount {inr(d['item_discount'] + d['bill_discount'])}"
                            f"  ·  GST {inr(d['gst_amount'])}  ·  Round off {inr(d['round_off'])}  ·  "
                            f"Returned {inr(d['returned'])}  ·  Paid (net) {inr(d['paid'])}  ·  "
                            f"Balance {inr(d['balance'])}")
        u = current_user()
        active = d["status"] == "COMPLETED"
        self.collect_btn.setVisible(active and d["balance"] > 0 and u.can("collect_payment"))
        self.return_btn.setVisible(active and u.can("returns"))
        self.cancel_btn.setVisible(active and u.can("cancel_sale") and not d["returns"])

    def _print(self, fmt):
        from ..output.pdf_docs import invoice_pdf
        from .printing import show_pdf
        path = run_safely(self, invoice_pdf, self.d, fmt)
        if path:
            show_pdf(path, self, f"Invoice {self.d['invoice_no']}")

    def _collect(self):
        dlg = PaymentDialog(self, f"Collect payment – {self.d['invoice_no']}", self.d["balance"])
        if dlg.exec() == QDialog.Accepted:
            amt, mode, note = dlg.result
            if run_safely(self, sales.collect_sale_payment, self.sale_id, amt, mode, note, new_ref(),
                          success=f"Payment of {inr(amt)} recorded", topic="sales"):
                self.load()

    def _cancel(self):
        from .widgets import ask_text
        reason = ask_text(self, "Cancel bill", f"Why is invoice {self.d['invoice_no']} being cancelled?\n"
                                               "Stock will be added back and the payment voided.")
        if reason is None:
            return
        if run_safely(self, sales.cancel_sale, self.sale_id, reason, success="Bill cancelled", topic="sales"):
            self.load()

    def _return(self):
        from .bus import go
        self.accept()
        go("returns", {"sale_id": self.sale_id})


# ================================================================ cash count
class CashCountDialog(QDialog):
    NOTES = [500, 200, 100, 50, 20, 10, 5, 2, 1]

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Count cash in drawer")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        grid = QGridLayout()
        self.spins = {}
        for r, n in enumerate(self.NOTES):
            sp = QSpinBox()
            sp.setRange(0, 100000)
            sp.valueChanged.connect(self._sum)
            self.spins[n] = sp
            grid.addWidget(QLabel(f"₹{n} {'notes' if n >= 10 else 'coins'}"), r, 0)
            grid.addWidget(QLabel("×"), r, 1)
            grid.addWidget(sp, r, 2)
            v = QLabel("₹0")
            v.setAlignment(Qt.AlignRight)
            self.spins[(n, "lbl")] = v
            grid.addWidget(v, r, 3)
        lay.addLayout(grid)
        self.total_lbl = label("Total: ₹0.00", "SectionTitle")
        lay.addWidget(self.total_lbl)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Use this total")
        bb.button(QDialogButtonBox.Ok).setProperty("variant", "primary")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.total = Decimal("0")

    def _sum(self):
        t = Decimal("0")
        for n in self.NOTES:
            v = self.spins[n].value() * n
            self.spins[(n, "lbl")].setText(inr(v, decimals=0))
            t += v
        self.total = t
        self.total_lbl.setText(f"Total: {inr(t)}")


SHORTCUTS = [
    ("Ctrl+D", "Dashboard"), ("Ctrl+B / F2", "New billing"), ("Ctrl+K", "Quick sale"), ("Ctrl+J", "New repair job"),
    ("Ctrl+P", "Products"), ("Ctrl+E", "Expenses"), ("Ctrl+L", "Live business summary"), ("F5", "Refresh screen"),
    ("F1", "This help"), ("— In billing —", ""), ("F3", "Search / scan product"), ("F4", "Customer"),
    ("F6", "Cash amount"), ("F9", "Save bill"), ("F10", "Save & print"), ("Ctrl+Delete", "Clear bill"),
    ("— In quick sale —", ""), ("Enter", "Save the sale"),
]


class ShortcutsDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Keyboard shortcuts")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 14)
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        for r, (k, v) in enumerate(SHORTCUTS):
            kl = QLabel(k)
            kl.setStyleSheet(f"font-weight: 700; color: {current().accent_text};")
            grid.addWidget(kl, r, 0)
            grid.addWidget(QLabel(v), r, 1)
        lay.addLayout(grid)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
