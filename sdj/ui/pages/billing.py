"""New Billing (POS): barcode/IMEI scan, multi-item cart, discounts, GST, split / partial / credit payments."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ...core import inventory, sales, settings
from ...core.calc import LineIn
from ...core.sales import CartItem
from ...db.base import D, q2
from ...output.fmt import inr
from .. import icons
from ..bus import changed, toast
from ..dialogs import ImeiSelectDialog
from ..printing import deliver_pdf, print_pdf, show_pdf
from ..theme import current
from ..widgets import (Card, CustomerPicker, MoneySpin, Page, ProductPicker, QtySpin, add_shortcut, button, confirm,
                       error, label, new_ref, run_approved, run_safely)

PRINT_CHOICES = [("Thermal 80 mm", "THERMAL_80"), ("Thermal 58 mm", "THERMAL_58"), ("A4 invoice", "A4"), ("No print", "NONE")]


class BillingPage(Page):
    subtitle = "Scan or search products, take payment and print – stock updates automatically"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.lines: list[dict] = []
        self._ref = new_ref()
        self._pay_touched = False
        self._calc = None
        self._edit = None          # {id, invoice_no, approver, allow: {product_id: qty already on the bill}}
        self.edit_bar = QWidget()
        self.edit_bar.setObjectName("EditBar")
        eb = QHBoxLayout(self.edit_bar)
        eb.setContentsMargins(14, 8, 10, 8)
        self.edit_lbl = label("", "", wrap=True)
        eb.addWidget(self.edit_lbl, 1)
        eb.addWidget(button("Stop editing", "", "x", lambda: self.clear_bill(ask=True)))
        self.edit_bar.setStyleSheet(f"#EditBar {{ background: {current().warn}22; border: 1px solid {current().warn};"
                                    " border-radius: 10px; }")
        self.edit_bar.setVisible(False)
        self.root.addWidget(self.edit_bar)
        body = QHBoxLayout()
        body.setSpacing(16)
        self.root.addLayout(body, 1)
        # ---------------- left: items
        left = QVBoxLayout()
        left.setSpacing(12)
        add = Card("Add items", "F3 to search · scan barcode or IMEI and press Enter")
        self.picker = ProductPicker()
        self.picker.setObjectName("BigInput")
        self.picker.picked.connect(self.add_product)
        add.add(self.picker)
        left.addWidget(add)
        cart = Card("Bill items")
        self.count_lbl = label("", "Muted")
        cart.header.addWidget(self.count_lbl)
        cart.header.addWidget(button("Clear bill", "ghost", "trash-2", self.clear_bill, "Ctrl+Delete"))
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["#", "Product", "Qty", "Price", "Discount", "GST", "Amount", ""])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(46)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        for i, w in ((0, 36), (2, 84), (3, 118), (4, 108), (5, 60), (6, 112), (7, 44)):
            self.table.setColumnWidth(i, w)
            hh.setSectionResizeMode(i, QHeaderView.Fixed)
        cart.add(self.table)
        self.empty_hint = label("The bill is empty. Search a product above or scan its barcode.", "Muted")
        self.empty_hint.setAlignment(Qt.AlignCenter)
        cart.add(self.empty_hint)
        self.notes = QLineEdit()
        self.notes.setPlaceholderText("Notes for this bill (optional)")
        cart.add(self.notes)
        left.addWidget(cart, 1)
        body.addLayout(left, 3)
        # ---------------- right: customer, totals, payment
        right = QVBoxLayout()
        right.setSpacing(12)
        cust = Card("Customer", "F4")
        self.customer = CustomerPicker()
        self.customer.changed.connect(self._customer_changed)
        self.cust_info = label("Walk-in customer", "Muted", wrap=True)
        cust.add(self.customer)
        cust.add(self.cust_info)
        right.addWidget(cust)
        summ = Card("Bill summary")
        g = QGridLayout()
        g.setVerticalSpacing(5)
        self.s = {}
        rows = [("gross", "Sub total"), ("item_disc", "Item discounts"), ("bill_disc", "Bill discount"),
                ("taxable", "Taxable value"), ("gst", "GST"), ("round", "Round off")]
        for r, (k, t) in enumerate(rows):
            g.addWidget(QLabel(t), r, 0)
            if k == "bill_disc":
                self.bill_disc = MoneySpin()
                self.bill_disc.valueChanged.connect(self.recalc)
                g.addWidget(self.bill_disc, r, 1)
            else:
                v = QLabel("₹0.00")
                v.setAlignment(Qt.AlignRight)
                self.s[k] = v
                g.addWidget(v, r, 1)
        summ.add(g)
        tl = label("GRAND TOTAL", "BigTotalLabel")
        self.total_lbl = label("₹0.00", "BigTotal")
        self.total_lbl.setAlignment(Qt.AlignRight)
        summ.add(tl)
        summ.add(self.total_lbl)
        right.addWidget(summ)
        pay = Card("Payment", "F6 cash · split if needed")
        pg = QGridLayout()
        pg.setVerticalSpacing(6)
        self.pay = {}
        for r, (m, t, ic) in enumerate((("CASH", "Cash", "banknote"), ("UPI", "UPI", "qr-code"),
                                        ("CARD", "Card", "credit-card"), ("OTHER", "Other", "wallet"))):
            lb = QLabel(t)
            sp = MoneySpin()
            sp.valueChanged.connect(self._pay_edited)
            full = QPushButton("Full")
            full.setProperty("variant", "chip")
            full.setIcon(icons.icon(ic, current().text2, 14))
            full.clicked.connect(lambda _=False, mm=m: self._full(mm))
            pg.addWidget(lb, r, 0)
            pg.addWidget(sp, r, 1)
            pg.addWidget(full, r, 2)
            self.pay[m] = sp
        pay.add(pg)
        self.paid_lbl = label("", "")
        self.balance_lbl = label("", "")
        pay.add(self.paid_lbl)
        pay.add(self.balance_lbl)
        tender = QHBoxLayout()
        tender.addWidget(QLabel("Cash tendered"))
        self.tendered = MoneySpin()
        self.tendered.valueChanged.connect(self._update_change)
        tender.addWidget(self.tendered)
        self.change_lbl = label("", "GoodText")
        tender.addWidget(self.change_lbl)
        pay.add(tender)
        right.addWidget(pay)
        self.print_choice = QComboBox()
        for t, v in PRINT_CHOICES:
            self.print_choice.addItem(t, v)
        default = settings.get("default_print_format") or "THERMAL_80"
        self.print_choice.setCurrentIndex(max(0, self.print_choice.findData(default)))
        pr = QHBoxLayout()
        pr.addWidget(QLabel("Print as"))
        pr.addWidget(self.print_choice, 1)
        right.addLayout(pr)
        act = QHBoxLayout()
        self.save_btn = button("Save (F9)", "", "save", lambda: self.save(False))
        self.print_btn = button("Save & Print (F10)", "primary", "printer", lambda: self.save(True))
        self.print_btn.setMinimumHeight(40)
        self.save_btn.setMinimumHeight(40)
        act.addWidget(self.save_btn)
        act.addWidget(self.print_btn, 1)
        right.addLayout(act)
        right.addStretch(1)
        rw = QWidget()
        rw.setLayout(right)
        rw.setFixedWidth(400)
        body.addWidget(rw)
        add_shortcut(self, "F3", lambda: self.picker.setFocus())
        add_shortcut(self, "F4", lambda: self.customer.edit.setFocus())
        add_shortcut(self, "F6", lambda: self.pay["CASH"].setFocus())
        add_shortcut(self, "F9", lambda: self.save(False))
        add_shortcut(self, "F10", lambda: self.save(True))
        add_shortcut(self, "Ctrl+Delete", self.clear_bill)
        self.render_lines()

    # ------------------------------------------------------------ data
    def refresh(self):
        self.picker.reload()
        self.customer.reload()

    def open_with(self, payload):
        if payload and payload.get("edit_sale_id"):
            self.start_edit(payload["edit_sale_id"], payload.get("approver"))
        self.picker.setFocus()

    def start_edit(self, sale_id: int, approver=None) -> None:
        """Load a saved bill of the open day; saving replaces it and keeps the invoice number."""
        d = sales.get_sale(sale_id)
        self.clear_bill(ask=False)
        allow: dict[int, int] = defaultdict(int)
        for ln in d["lines"]:
            p = inventory.get_product(ln["product_id"])
            allow[p["id"]] += ln["quantity"]
            line = dict(product=p, qty=ln["quantity"], price=D(ln["unit_price"]), disc=D(ln["item_discount"]))
            if ln.get("imei_record_id"):
                line.update(imei_id=ln["imei_record_id"], imei=ln["imei"])
            self.lines.append(line)
        self._edit = dict(id=sale_id, invoice_no=d["invoice_no"], approver=approver, allow=dict(allow))
        self.bill_disc.set_d(d["bill_discount"])
        if not d["customer"]["is_walk_in"]:
            self.customer.set_customer(dict(id=d["customer_id"], name=d["customer_name"], phone=d["customer_phone"]))
        self.notes.setText(d.get("notes") or "")
        paid = defaultdict(Decimal)
        for p in d["payments"]:
            if p["status"] == "ACTIVE" and p["direction"] == "IN" and p["mode"] in self.pay:
                paid[p["mode"]] += D(p["amount"])
        self._pay_touched = True
        self.render_lines()
        for m, sp in self.pay.items():
            sp.blockSignals(True)
            sp.set_d(paid.get(m, 0))
            sp.blockSignals(False)
        self._update_paid()
        self.edit_lbl.setText(f"<b>Editing invoice {d['invoice_no']}</b> – change items, prices or payment and save. "
                              "The invoice number stays the same; the old version is kept as cancelled.")
        self.edit_bar.setVisible(True)

    def _avail(self, prod: dict) -> int:
        extra = self._edit["allow"].get(prod["id"], 0) if self._edit else 0
        return prod["quantity"] + extra

    def _stock_in_cart(self, pid: int) -> int:
        return sum(l["qty"] for l in self.lines if l["product"]["id"] == pid)

    def add_product(self, p: dict):
        p = inventory.get_product(p["id"]) | ({"_imei": p["_imei"]} if p.get("_imei") else {})
        if p["track_imei"]:
            used = {l["imei_id"] for l in self.lines if l.get("imei_id")}
            if p.get("_imei"):
                if p["_imei"]["id"] in used:
                    toast("That phone is already in the bill.", "error")
                    return
                sel = [dict(id=p["_imei"]["id"], label=p["_imei"]["imei1"])]
            else:
                dlg = ImeiSelectDialog(self, p, 1, used)
                if dlg.exec() != QDialog.Accepted:
                    return
                sel = dlg.selected
            for r in sel:
                self.lines.append(dict(product=p, qty=1, price=D(p["selling_price"]), disc=Decimal("0"),
                                       imei_id=r["id"], imei=r["label"]))
        else:
            if self._stock_in_cart(p["id"]) + 1 > self._avail(p):
                toast(f"'{p['name']}' – only {self._avail(p)} in stock.", "error")
                return
            for l in self.lines:
                if l["product"]["id"] == p["id"] and not l.get("imei_id"):
                    l["qty"] += 1
                    break
            else:
                self.lines.append(dict(product=p, qty=1, price=D(p["selling_price"]), disc=Decimal("0")))
        self.render_lines()
        self.picker.setFocus()

    def render_lines(self):
        self.table.setRowCount(len(self.lines))
        p = current()
        for r, l in enumerate(self.lines):
            prod = l["product"]
            self.table.setItem(r, 0, QTableWidgetItem(str(r + 1)))
            name = QLabel(f"<b>{prod['name']}</b><br><span style='color:{p.muted}; font-size:8.5pt'>"
                          f"{('IMEI ' + l['imei']) if l.get('imei') else (prod['code'] + ' · stock ' + str(prod['quantity']))}</span>")
            name.setContentsMargins(4, 0, 4, 0)
            self.table.setCellWidget(r, 1, name)
            q = QtySpin(maximum=max(self._avail(prod), 1))
            q.setValue(l["qty"])
            q.setEnabled(not l.get("imei_id"))
            q.valueChanged.connect(lambda v, i=r: self._set(i, "qty", v))
            self.table.setCellWidget(r, 2, q)
            pr = MoneySpin()
            pr.set_d(l["price"])
            pr.valueChanged.connect(lambda v, i=r, w=pr: self._set(i, "price", w.value_d()))
            self.table.setCellWidget(r, 3, pr)
            dc = MoneySpin()
            dc.set_d(l["disc"])
            dc.valueChanged.connect(lambda v, i=r, w=dc: self._set(i, "disc", w.value_d()))
            self.table.setCellWidget(r, 4, dc)
            gst = QTableWidgetItem(f"{prod['gst_rate']:.0f}%" if settings.get("gst_enabled") else "—")
            gst.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(r, 5, gst)
            amt = QTableWidgetItem("")
            amt.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(r, 6, amt)
            rm = QPushButton()
            rm.setIcon(icons.icon("x", p.bad, 16))
            rm.setToolTip("Remove")
            rm.setFlat(True)
            rm.clicked.connect(lambda _=False, i=r: self._remove(i))
            self.table.setCellWidget(r, 7, rm)
        self.empty_hint.setVisible(not self.lines)
        self.table.setVisible(bool(self.lines))
        self.recalc()

    def _set(self, i: int, key: str, value):
        if i >= len(self.lines):
            return
        self.lines[i][key] = value
        self.recalc()

    def _remove(self, i: int):
        if i < len(self.lines):
            self.lines.pop(i)
            self.render_lines()

    def recalc(self):
        lines = [LineIn(l["qty"], l["price"], min(l["disc"], q2(l["price"] * l["qty"])), l["product"]["gst_rate"])
                 for l in self.lines]
        calc = sales.preview_bill(lines, self.bill_disc.value_d())
        self._calc = calc
        for r, lo in enumerate(calc.lines):
            it = self.table.item(r, 6)
            if it:
                it.setText(inr(lo.line_total))
        self.s["gross"].setText(inr(calc.gross))
        self.s["item_disc"].setText("-" + inr(calc.item_discount) if calc.item_discount else inr(0))
        self.s["taxable"].setText(inr(calc.taxable))
        self.s["gst"].setText(inr(calc.gst) + (f"  (CGST {inr(calc.cgst)} + SGST {inr(calc.sgst)})" if calc.gst else ""))
        self.s["round"].setText(inr(calc.round_off))
        self.total_lbl.setText(inr(calc.grand_total))
        n_items = sum(l["qty"] for l in self.lines)
        self.count_lbl.setText(f"{len(self.lines)} line(s) · {n_items} item(s)")
        if not self._pay_touched:
            for m, sp in self.pay.items():
                sp.blockSignals(True)
                sp.set_d(calc.grand_total if m == "CASH" else 0)
                sp.blockSignals(False)
        self._update_paid()

    def _pay_edited(self):
        self._pay_touched = True
        self._update_paid()

    def _full(self, mode: str):
        self._pay_touched = True
        total = self._calc.grand_total if self._calc else Decimal("0")
        for m, sp in self.pay.items():
            sp.blockSignals(True)
            sp.set_d(total if m == mode else 0)
            sp.blockSignals(False)
        self._update_paid()

    def _paid(self) -> Decimal:
        return sum((sp.value_d() for sp in self.pay.values()), Decimal("0.00"))

    def _update_paid(self):
        total = self._calc.grand_total if self._calc else Decimal("0")
        paid = self._paid()
        bal = total - paid
        p = current()
        self.paid_lbl.setText(f"Paid now: <b>{inr(paid)}</b>")
        if bal > 0:
            self.balance_lbl.setText(f"<span style='color:{p.warn}'><b>Balance on credit: {inr(bal)}</b></span>"
                                     + ("" if self.customer.customer_id() else "  – select a customer"))
        elif bal < 0:
            self.balance_lbl.setText(f"<span style='color:{p.bad}'><b>Paid {inr(-bal)} more than the total</b></span>")
        else:
            self.balance_lbl.setText(f"<span style='color:{p.good}'><b>Fully paid</b></span>" if total > 0 else "")
        self._update_change()

    def _update_change(self):
        t = self.tendered.value_d()
        cash = self.pay["CASH"].value_d()
        self.change_lbl.setText(f"Return {inr(t - cash)}" if t > cash > 0 else "")

    def _customer_changed(self, c):
        if c:
            from ...core.parties import customer_balance
            bal = customer_balance(c["id"])
            self.cust_info.setText(f"{c['name']} · {c.get('phone', '')}" + (f" · existing dues {inr(bal)}" if bal > 0 else ""))
        else:
            self.cust_info.setText("Walk-in customer")
        self._update_paid()

    # ------------------------------------------------------------ actions
    def clear_bill(self, ask: bool = True):
        if ask and self.lines and not confirm(self, "Clear bill", "Remove all items from this bill?", "Clear"):
            return
        self.lines = []
        self._ref = new_ref()
        self._pay_touched = False
        self._edit = None
        self.edit_bar.setVisible(False)
        self.bill_disc.setValue(0)
        self.tendered.setValue(0)
        self.notes.clear()
        self.customer.set_customer(None)
        self.render_lines()
        self.picker.setFocus()

    def save(self, with_print: bool):
        if not self.lines:
            error(self, "Add at least one product to the bill.")
            return
        items = [CartItem(product_id=l["product"]["id"], quantity=l["qty"], unit_price=l["price"],
                          discount=min(l["disc"], q2(l["price"] * l["qty"])), imei_record_id=l.get("imei_id"))
                 for l in self.lines]
        payments = {m: sp.value_d() for m, sp in self.pay.items() if sp.value_d() > 0}
        self.save_btn.setEnabled(False)
        self.print_btn.setEnabled(False)
        common = dict(customer_id=self.customer.customer_id(), bill_discount=self.bill_disc.value_d(),
                      payments=payments, notes=self.notes.text(), client_ref=self._ref, topic="sales")
        editing = self._edit
        try:
            if editing:
                from ...core.auth import approved
                if editing.get("approver") is not None:
                    with approved(editing["approver"], {"cancel_sale"}):
                        res = run_approved(self, sales.amend_sale, editing["id"], items, **common)
                else:
                    res = run_approved(self, sales.amend_sale, editing["id"], items, **common)
            else:
                res = run_approved(self, sales.create_sale, items, **common)
        finally:
            self.save_btn.setEnabled(True)
            self.print_btn.setEnabled(True)
        if not res:
            return
        msg = (f"Invoice {res['invoice_no']} updated · {inr(res['grand_total'])}" if editing else
               f"Invoice {res['invoice_no']} saved · {inr(res['grand_total'])}")
        if res["balance"] > 0:
            msg += f" · {inr(res['balance'])} on credit"
        toast(msg)
        fmt = self.print_choice.currentData()
        self.clear_bill(ask=False)
        if fmt != "NONE" and (with_print or (settings.get("print_after_save") or "ASK") != "NONE"):
            from ...output.pdf_docs import invoice_pdf
            path = run_safely(self, invoice_pdf, sales.get_sale(res["id"]), fmt)
            if path:
                if with_print:
                    print_pdf(path, self)
                else:
                    deliver_pdf(path, self, f"Invoice {res['invoice_no']}")
