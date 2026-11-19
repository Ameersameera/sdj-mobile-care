"""Returns & exchanges: full/partial returns, refunds, damaged goods, exchanges – linked to the original invoice."""
from __future__ import annotations

from decimal import Decimal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
                               QRadioButton, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from ...core import inventory, returns, sales
from ...core.returns import RETURN_REASONS
from ...core.calc import LineIn
from ...core.sales import CartItem
from ...db.base import D, q2
from ...output.fmt import d_only, dt, inr
from .. import icons
from ..bus import toast
from ..dialogs import ImeiSelectDialog
from ..printing import show_pdf
from ..theme import current
from ..widgets import (Card, Col, DataTable, DateRangeBar, ModeButtons, MoneySpin, Page, ProductPicker, button, confirm,
                       error, hbox, label, new_ref, run_safely)


class ReturnsPage(Page):
    subtitle = "Every return is linked to its original invoice; stock and accounts adjust automatically"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sale = None
        self.ex_lines: list[dict] = []
        self._ref = new_ref()
        self.tabs = QTabWidget()
        self.root.addWidget(self.tabs, 1)
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 10, 0, 0)
        lay.setSpacing(12)
        find = Card("Find the invoice")
        self.inv = QLineEdit()
        self.inv.setObjectName("BigInput")
        self.inv.setPlaceholderText("Invoice number (e.g. SDJ/26-27/00012 or just 12) or IMEI of a phone sold")
        self.inv.returnPressed.connect(self.find)
        find.add(hbox(self.inv, button("Find", "primary", "search", self.find)))
        self.sale_info = label("", "Muted", wrap=True)
        find.add(self.sale_info)
        lay.addWidget(find)
        body = QHBoxLayout()
        body.setSpacing(14)
        items = Card("Items to return")
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Item", "Sold", "Returned", "Return qty", "Condition", "Unit value", "Value"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for i, wd in ((1, 55), (2, 75), (3, 95), (4, 120), (5, 105), (6, 105)):
            self.table.setColumnWidth(i, wd)
            hh.setSectionResizeMode(i, QHeaderView.Fixed)
        items.add(self.table)
        self.reason = QComboBox()
        self.reason.setEditable(True)
        self.reason.addItems(RETURN_REASONS)
        items.add(hbox(QLabel("Reason"), self.reason, None))
        body.addWidget(items, 3)
        act = Card("Refund or exchange")
        self.r_refund = QRadioButton("Return / refund")
        self.r_exchange = QRadioButton("Exchange for other product(s)")
        self.r_refund.setChecked(True)
        act.add(self.r_refund)
        act.add(self.r_exchange)
        self.value_lbl = label("", "SectionTitle")
        self.refund_lbl = label("", "Muted", wrap=True)
        act.add(self.value_lbl)
        act.add(self.refund_lbl)
        self.refund_box = QWidget()
        rb = QVBoxLayout(self.refund_box)
        rb.setContentsMargins(0, 0, 0, 0)
        self.refund_amt = MoneySpin()
        self.refund_mode = ModeButtons()
        rb.addLayout(hbox(QLabel("Refund amount"), self.refund_amt))
        rb.addWidget(self.refund_mode)
        act.add(self.refund_box)
        self.ex_box = QWidget()
        eb = QVBoxLayout(self.ex_box)
        eb.setContentsMargins(0, 0, 0, 0)
        self.ex_picker = ProductPicker(in_stock_only=True, placeholder="Add exchange product…")
        self.ex_picker.picked.connect(self.add_exchange)
        self.ex_list = QLabel("")
        self.ex_list.setWordWrap(True)
        self.ex_pay = MoneySpin()
        self.ex_mode = ModeButtons()
        eb.addWidget(self.ex_picker)
        eb.addWidget(self.ex_list)
        eb.addWidget(button("Clear exchange items", "ghost", "trash-2", self.clear_exchange))
        eb.addLayout(hbox(QLabel("Customer pays difference"), self.ex_pay))
        eb.addWidget(self.ex_mode)
        act.add(self.ex_box)
        self.process_btn = button("Process return", "primary", "check", self.process)
        act.add(self.process_btn)
        act.lay.addStretch(1)
        body.addWidget(act, 2)
        lay.addLayout(body, 1)
        self.r_refund.toggled.connect(self._mode)
        self.tabs.addTab(w, "New return / exchange")
        w2 = QWidget()
        l2 = QVBoxLayout(w2)
        l2.setContentsMargins(0, 10, 0, 0)
        c2 = Card()
        self.range = DateRangeBar("This month")
        self.range.changed.connect(lambda *_: self.refresh())
        self.hist = DataTable([Col("business_date", "Date", "date", 100), Col("return_no", "Credit note", width=140, bold=True),
                               Col("invoice_no", "Invoice", width=140), Col("customer", "Customer", width=150),
                               Col("type", "Type", width=90), Col("items", "Items", width=220), Col("reason", "Reason", width=160),
                               Col("value", "Value", "money", 100), Col("refund", "Refund", "money", 95),
                               Col("exchange_invoice", "Exchange bill", width=140)], placeholder="Search returns…", stretch="items")
        self.hist.add_top(self.range)
        c2.add(self.hist)
        c2.add(hbox(button("Print credit note", "", "printer", self.print_cn), None))
        l2.addWidget(c2)
        self.tabs.addTab(w2, "Return history")
        w3 = QWidget()
        l3 = QVBoxLayout(w3)
        l3.setContentsMargins(0, 10, 0, 0)
        c3 = Card("Damaged items received back", "not returned to sellable stock")
        self.damaged = DataTable([Col("business_date", "Date", "date", 100), Col("return_no", "Credit note", width=140),
                                  Col("product", "Product", width=220, bold=True), Col("quantity", "Qty", "int", 60),
                                  Col("imei", "IMEI", width=150), Col("cost", "Cost value", "money", 110),
                                  Col("reason", "Reason", width=200)], search=True, stretch="product")
        c3.add(self.damaged)
        l3.addWidget(c3)
        self.tabs.addTab(w3, "Damaged items")
        self.tabs.currentChanged.connect(lambda *_: self.refresh())
        self._mode()

    def _mode(self):
        ex = self.r_exchange.isChecked()
        self.ex_box.setVisible(ex)
        self.refund_box.setVisible(not ex)
        self.process_btn.setText("Process exchange" if ex else "Process return")
        self._update()

    def refresh(self):
        i = self.tabs.currentIndex()
        if i == 1:
            a, b = self.range.range()
            self.hist.set_rows(returns.list_returns(a, b))
        elif i == 2:
            self.damaged.set_rows(returns.damaged_items())
        else:
            self.ex_picker.reload()

    def open_with(self, payload):
        if isinstance(payload, dict) and payload.get("sale_id"):
            self.tabs.setCurrentIndex(0)
            self.load_sale(payload["sale_id"])

    def find(self):
        sid = sales.find_sale_id(self.inv.text())
        if not sid:
            error(self, "No invoice found for that number / IMEI.")
            return
        self.load_sale(sid)

    def load_sale(self, sid):
        d = run_safely(self, returns.returnable_lines, sid)
        if not d or d is True:
            return
        self.sale = d
        self._ref = new_ref()
        self.inv.setText(d["invoice_no"])
        self.sale_info.setText(f"<b>{d['invoice_no']}</b> · {dt(d['created_at'])} · {d['customer_name']} {d['customer_phone']} · "
                               f"total {inr(d['grand_total'])} · paid {inr(d['paid'])} · returned so far {inr(d['returned'])} · "
                               f"balance {inr(d['balance'])}")
        self.table.setRowCount(len(d["lines"]))
        self.spins = []
        for r, ln in enumerate(d["lines"]):
            name = ln["product_name"] + (f"  (IMEI {ln['imei']})" if ln.get("imei") else "")
            self.table.setItem(r, 0, QTableWidgetItem("  " + name))
            for c, v in ((1, ln["quantity"]), (2, ln["returned_qty"])):
                it = QTableWidgetItem(str(v))
                it.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(r, c, it)
            sp = QSpinBox()
            sp.setRange(0, ln["returnable_qty"])
            sp.setEnabled(ln["returnable_qty"] > 0)
            sp.valueChanged.connect(self._update)
            self.table.setCellWidget(r, 3, sp)
            cond = QComboBox()
            cond.addItem("Resalable", "RESALABLE")
            cond.addItem("Damaged", "DAMAGED")
            self.table.setCellWidget(r, 4, cond)
            it = QTableWidgetItem(inr(ln["unit_value"]))
            it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(r, 5, it)
            v = QTableWidgetItem("")
            v.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(r, 6, v)
            self.spins.append((ln, sp, cond))
        self._update()

    def _chosen(self):
        if not self.sale:
            return []
        return [dict(sale_item_id=ln["id"], quantity=sp.value(), condition=cond.currentData())
                for ln, sp, cond in self.spins if sp.value() > 0]

    def _ex_total(self) -> Decimal:
        lines = [LineIn(l["qty"], l["price"], Decimal(0), l["product"]["gst_rate"]) for l in self.ex_lines]
        return sales.preview_bill(lines).grand_total if lines else Decimal("0.00")

    def _update(self):
        if not self.sale:
            self.value_lbl.setText("Find an invoice to start.")
            self.refund_lbl.setText("")
            return
        for r, (ln, sp, cond) in enumerate(self.spins):
            it = self.table.item(r, 6)
            if it:
                it.setText(inr(q2(ln["unit_value"] * sp.value())) if sp.value() else "")
        chosen = self._chosen()
        if not chosen:
            self.value_lbl.setText("Select the quantity to return.")
            self.refund_lbl.setText("")
            return
        pv = returns.preview_return(self.sale["id"], chosen)
        self.preview = pv
        self.value_lbl.setText(f"Return value: {inr(pv['value'])}")
        if self.r_exchange.isChecked():
            ex_total = self._ex_total()
            credit = min(pv["refundable"], ex_total)
            diff = ex_total - credit
            refund_left = pv["refundable"] - credit
            self.ex_pay.set_d(diff)
            self.refund_lbl.setText(f"Credit available {inr(pv['refundable'])} · new items {inr(ex_total)} · "
                                    + (f"customer pays {inr(diff)}" if diff > 0 else f"refund {inr(refund_left)}"))
        else:
            self.refund_amt.setMaximum(float(pv["refundable"]))
            self.refund_amt.set_d(pv["refundable"])
            txt = f"Refundable now: {inr(pv['refundable'])}"
            if pv["due_reduction"] > 0:
                txt += f" · reduces the customer's pending balance by {inr(pv['due_reduction'])}"
            self.refund_lbl.setText(txt)

    def add_exchange(self, p):
        p = inventory.get_product(p["id"]) | ({"_imei": p.get("_imei")} if p.get("_imei") else {})
        line = dict(product=p, qty=1, price=D(p["selling_price"]))
        if p["track_imei"]:
            used = {l.get("imei_id") for l in self.ex_lines}
            if p.get("_imei"):
                line["imei_id"] = p["_imei"]["id"]
            else:
                dlg = ImeiSelectDialog(self, p, 1, used)
                if dlg.exec() != QDialog.Accepted:
                    return
                line["imei_id"] = dlg.selected[0]["id"]
        self.ex_lines.append(line)
        self.ex_list.setText("<br>".join(f"• {l['product']['name']} – {inr(l['price'])}" for l in self.ex_lines))
        self._update()

    def clear_exchange(self):
        self.ex_lines = []
        self.ex_list.setText("")
        self._update()

    def process(self):
        chosen = self._chosen()
        if not self.sale or not chosen:
            error(self, "Find the invoice and select what is being returned.")
            return
        kwargs = dict(reason=self.reason.currentText(), client_ref=self._ref)
        if self.r_exchange.isChecked():
            if not self.ex_lines:
                error(self, "Add the product(s) the customer is taking in exchange.")
                return
            kwargs["exchange_items"] = [CartItem(l["product"]["id"], l["qty"], l["price"], imei_record_id=l.get("imei_id"))
                                        for l in self.ex_lines]
            kwargs["exchange_payments"] = {self.ex_mode.value(): self.ex_pay.value_d()} if self.ex_pay.value_d() > 0 else {}
            kwargs["refund_mode"] = self.ex_mode.value()
        else:
            kwargs["refund_amount"] = self.refund_amt.value_d()
            kwargs["refund_mode"] = self.refund_mode.value()
        if not confirm(self, "Confirm", f"Process this {'exchange' if self.r_exchange.isChecked() else 'return'} for "
                                        f"{self.sale['invoice_no']}?"):
            return
        res = run_safely(self, returns.create_return, self.sale["id"], chosen, topic="sales", **kwargs)
        if not res:
            return
        msg = f"Credit note {res['return_no']} · value {inr(res['value'])}"
        if res["refund"]:
            msg += f" · refund {inr(res['refund'])}"
        if res.get("exchange_invoice_no"):
            msg += f" · exchange bill {res['exchange_invoice_no']}"
        toast(msg)
        self._print_cn_for(res["id"])
        self.sale = None
        self.ex_lines = []
        self.ex_list.setText("")
        self.table.setRowCount(0)
        self.sale_info.setText("")
        self.inv.clear()
        self._update()

    def _print_cn_for(self, return_id):
        rows = [r for r in returns.list_returns() if r["id"] == return_id]
        if not rows:
            return
        r = rows[0]
        from ...db.base import session_scope
        from ...db.models import SaleReturn
        with session_scope() as s:
            ret = s.get(SaleReturn, return_id)
            items = [dict(product=i.product_name, quantity=i.quantity, condition=i.condition, value=i.total_value) for i in ret.items]
        data = dict(return_no=r["return_no"], date=r["business_date"], invoice_no=r["invoice_no"], customer=r["customer"],
                    items=items, value=r["value"], refund=r["refund"], refund_mode=r["refund_mode"],
                    exchange_invoice=r["exchange_invoice"], reason=r["reason"])
        from ...core import settings
        from ...output.pdf_docs import credit_note_pdf
        fmt = settings.get("default_print_format") or "THERMAL_80"
        path = run_safely(self, credit_note_pdf, data, fmt)
        if path:
            show_pdf(path, self, f"Credit note {r['return_no']}")

    def print_cn(self):
        r = self.hist.selected()
        if r:
            self._print_cn_for(r["id"])
