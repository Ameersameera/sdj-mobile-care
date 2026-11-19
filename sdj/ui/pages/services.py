"""Mobile service & repair: job intake, parts (auto stock deduction), labour, status, payments, delivery."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QFormLayout, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QSpinBox, QVBoxLayout, QWidget)

from ...core import inventory, repairs, settings
from ...core.repairs import JOB_STATUSES, OPEN_STATUSES, STATUS_LABELS
from ...output.fmt import d_only, dt, inr
from .. import icons
from ..bus import changed, toast
from ..printing import show_pdf
from ..theme import current
from ..widgets import (Card, Col, DataTable, DateEdit, FormDialog, ModeButtons, MoneySpin, Page, ProductPicker,
                       QtySpin, ask_text, button, confirm, error, hbox, label, multiline, new_ref, run_safely)


def print_job(parent, job_id: int, kind: str, fmt: str | None = None):
    from ...output.pdf_docs import job_pdf
    fmt = fmt or settings.get("default_print_format") or "THERMAL_80"
    path = run_safely(parent, job_pdf, repairs.get_job(job_id), kind, fmt)
    if path:
        show_pdf(path, parent, "Job card" if kind == "JOB_CARD" else "Service bill")


class NewJobDialog(FormDialog):
    def __init__(self, parent):
        super().__init__(parent, "New repair job", 560)
        self._ref = new_ref()
        self.phone = QLineEdit()
        self.phone.setPlaceholderText("Customer mobile number *")
        self.name = QLineEdit()
        self.name.setPlaceholderText("Customer name *")
        self.brand = QComboBox()
        self.brand.setEditable(True)
        self.brand.addItems(["", "Samsung", "Xiaomi / Redmi", "Vivo", "Oppo", "Realme", "Apple", "OnePlus", "Motorola",
                             "Nokia", "iQOO", "Poco", "Infinix", "Tecno", "Google", "Other"])
        self.model = QLineEdit()
        self.model.setPlaceholderText("e.g. Note 12, A15, iPhone 11")
        self.imei = QLineEdit()
        self.imei.setPlaceholderText("Optional")
        self.accessories = QLineEdit()
        self.accessories.setPlaceholderText("e.g. back cover, SIM tray, charger")
        self.problem = multiline("What did the customer report? *", 70)
        self.tech = QComboBox()
        self.tech.setEditable(True)
        self.tech.addItems([""] + repairs.technicians())
        self.est = DateEdit(date.today() + timedelta(days=1))
        self.labour = MoneySpin()
        self.warranty = QSpinBox()
        self.warranty.setRange(0, 365)
        self.warranty.setSuffix(" days")
        self.warranty.setValue(int(settings.get("default_repair_warranty_days") or 0))
        self.advance = MoneySpin()
        self.adv_mode = ModeButtons()
        for t, w in (("Phone *", self.phone), ("Name *", self.name), ("Mobile brand *", self.brand), ("Model", self.model),
                     ("IMEI", self.imei), ("Accessories received", self.accessories), ("Problem *", self.problem),
                     ("Technician", self.tech), ("Estimated delivery", self.est), ("Estimated labour", self.labour),
                     ("Repair warranty", self.warranty), ("Advance received", self.advance), ("Advance mode", self.adv_mode)):
            self.form.addRow(t, w)
        self.phone.editingFinished.connect(self._lookup)
        self.ok_btn.setText("Create job && print job card")
        self.finish_layout()
        self.phone.setFocus()

    def _lookup(self):
        from ...core.parties import search_customers
        ph = self.phone.text().strip()
        if len(ph) >= 6 and not self.name.text():
            res = [c for c in search_customers(ph) if c["phone"] == ph]
            if res:
                self.name.setText(res[0]["name"])

    def on_save(self):
        data = dict(customer_name=self.name.text(), customer_phone=self.phone.text(), brand=self.brand.currentText(),
                    model=self.model.text(), imei=self.imei.text(), accessories=self.accessories.text(),
                    problem=self.problem.toPlainText(), technician=self.tech.currentText(),
                    estimated_delivery=self.est.value(), labour_charge=self.labour.value_d(),
                    warranty_days=self.warranty.value())
        self.res = repairs.create_job(data, advance=self.advance.value_d(), advance_mode=self.adv_mode.value(),
                                      client_ref=self._ref)
        changed("service")


class JobDialog(QDialog):
    """Work on a job: details, status, parts used, labour, payments, delivery and printing."""

    def __init__(self, parent, job_id: int):
        super().__init__(parent)
        self.job_id = job_id
        self.resize(1040, 720)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(10)
        self.head = label("", "PageTitle")
        self.sub = label("", "Muted", wrap=True)
        root.addWidget(self.head)
        root.addWidget(self.sub)
        body = QHBoxLayout()
        body.setSpacing(14)
        root.addLayout(body, 1)
        left = QVBoxLayout()
        det = Card("Device & repair")
        f = QFormLayout()
        f.setVerticalSpacing(8)
        self.status = QComboBox()
        for s in JOB_STATUSES:
            self.status.addItem(STATUS_LABELS[s], s)
        self.tech = QComboBox()
        self.tech.setEditable(True)
        self.tech.addItems([""] + repairs.technicians())
        self.problem = multiline("", 54)
        self.diagnosis = multiline("Technician findings / work done", 54)
        self.est = DateEdit()
        self.labour = MoneySpin()
        self.discount = MoneySpin()
        self.warranty = QSpinBox()
        self.warranty.setRange(0, 365)
        self.warranty.setSuffix(" days")
        f.addRow("Status", self.status)
        f.addRow("Technician", self.tech)
        f.addRow("Reported problem", self.problem)
        f.addRow("Diagnosis / work", self.diagnosis)
        f.addRow("Est. delivery", self.est)
        f.addRow("Labour charge", self.labour)
        f.addRow("Discount", self.discount)
        f.addRow("Repair warranty", self.warranty)
        det.add(f)
        self.save_btn = button("Save changes", "primary", "save", self.save_details)
        det.add(hbox(None, self.save_btn))
        left.addWidget(det)
        left.addStretch(1)
        body.addLayout(left, 2)
        right = QVBoxLayout()
        parts = Card("Spare parts used", "stock is deducted immediately")
        self.part_picker = ProductPicker(group="SPARE", in_stock_only=True, exclude_imei=True,
                                         placeholder="Add spare part – search or scan…")
        self.part_picker.picked.connect(self._pick_part)
        self.part_qty = QtySpin()
        self.part_price = MoneySpin()
        self.add_part_btn = button("Add part", "", "plus", self.add_part)
        self._part = None
        self.part_lbl = label("", "Muted")
        parts.add(self.part_picker)
        parts.add(hbox(self.part_lbl, None, QLabel("Qty"), self.part_qty, QLabel("Charge"), self.part_price, self.add_part_btn))
        self.parts = DataTable([Col("product_name", "Part", width=220, bold=True), Col("quantity", "Qty", "int", 50),
                                Col("unit_price", "Charge", "money", 95), Col("amount", "Amount", "money", 100),
                                Col("cost", "Cost", "money", 90), Col("status", "Status", "status", 90)],
                               search=False, stretch="product_name")
        self.parts.setMinimumHeight(170)
        parts.add(self.parts)
        parts.add(hbox(None, button("Remove selected part", "danger", "x", self.remove_part)))
        right.addWidget(parts, 1)
        money = Card("Bill & payments")
        self.money_lbl = label("", "")
        self.money_lbl.setTextFormat(Qt.RichText)
        money.add(self.money_lbl)
        self.pays = DataTable([Col("created_at", "Time", "datetime", 150), Col("kind", "Type", width=140),
                               Col("mode", "Mode", width=70), Col("direction", "In/Out", width=60),
                               Col("amount", "Amount", "money", 100)], search=False, stretch="kind")
        self.pays.setMinimumHeight(120)
        money.add(self.pays)
        right.addWidget(money)
        body.addLayout(right, 3)
        bar = QHBoxLayout()
        bar.addWidget(button("Job card", "", "printer", lambda: print_job(self, self.job_id, "JOB_CARD")))
        bar.addWidget(button("Service bill", "", "file-text", lambda: print_job(self, self.job_id, "RECEIPT")))
        bar.addWidget(button("Service bill (A4)", "", "file-text", lambda: print_job(self, self.job_id, "RECEIPT", "A4")))
        bar.addStretch(1)
        self.pay_btn = button("Add payment", "", "hand-coins", self.add_payment)
        self.ready_btn = button("Mark ready", "", "circle-check", lambda: self.set_status("READY"))
        self.deliver_btn = button("Deliver & collect", "primary", "check", self.deliver)
        self.cancel_btn = button("Cancel job", "danger", "x", self.cancel_job)
        for b in (self.pay_btn, self.ready_btn, self.deliver_btn, self.cancel_btn):
            bar.addWidget(b)
        bar.addWidget(button("Close", "", None, self.accept))
        root.addLayout(bar)
        self.load()

    def load(self):
        j = repairs.get_job(self.job_id)
        self.j = j
        self.setWindowTitle(f"Repair job {j['job_no']}")
        self.head.setText(f"{j['job_no']} · {j['device']} · {j['status_label']}")
        self.sub.setText(f"{j['customer_name']} · {j['customer_phone']} · received {d_only(j['received_date'])}"
                         + (f" · IMEI {j['imei']}" if j["imei"] else "") + (f" · accessories: {j['accessories']}" if j["accessories"] else "")
                         + (f" · completed {d_only(j['completed_date'])}" if j["completed_date"] else "")
                         + (f" · warranty till {d_only(j['warranty_expiry'])}" if j.get("warranty_expiry") else "")
                         + (f"\nCancelled: {j['cancel_reason']}" if j["cancel_reason"] else ""))
        self.status.blockSignals(True)
        self.status.setCurrentIndex(self.status.findData(j["status"]))
        self.status.blockSignals(False)
        self.tech.setCurrentText(j["technician"])
        self.problem.setPlainText(j["problem"])
        self.diagnosis.setPlainText(j["diagnosis"])
        if j["estimated_delivery"]:
            self.est.set_date(j["estimated_delivery"])
        self.labour.set_d(j["labour"])
        self.discount.set_d(j["discount"])
        self.warranty.setValue(j["warranty_days"])
        self.parts.set_rows(j["items"])
        self.pays.set_rows(j["payments"])
        pal = current()
        bal = j["total"] - j["paid"]
        gst = j["labour_gst"] + j["parts_gst"]
        self.money_lbl.setText(
            f"Labour {inr(j['labour'])} &nbsp;+&nbsp; Parts {inr(j['parts'])} &nbsp;−&nbsp; Discount {inr(j['discount'])}"
            + (f" &nbsp;(GST incl. {inr(gst)})" if gst else "") +
            f"<br><span style='font-size:15pt; font-weight:800'>Total {inr(j['total'])}</span> &nbsp; "
            f"Paid {inr(j['paid'])} &nbsp; <b style='color:{pal.warn if bal > 0 else pal.good}'>Balance {inr(bal)}</b>"
            + ("" if j["completed_date"] else f"<br><span style='color:{pal.muted}'>Income is booked when the job is marked ready / delivered.</span>"))
        final = j["status"] in ("DELIVERED", "CANCELLED")
        done = j["status"] in ("READY", "DELIVERED")
        for w in (self.status, self.tech, self.problem, self.diagnosis, self.est, self.warranty, self.save_btn):
            w.setEnabled(not final)
        for w in (self.labour, self.discount):
            w.setEnabled(not final and not done)
        for w in (self.part_picker, self.part_qty, self.part_price, self.add_part_btn):
            w.setEnabled(not final and not done)
        self.pay_btn.setEnabled(not final or bal > 0)
        self.ready_btn.setVisible(j["status"] in OPEN_STATUSES)
        self.deliver_btn.setVisible(not final)
        self.cancel_btn.setVisible(not final)

    def _pick_part(self, p):
        p = inventory.get_product(p["id"])
        self._part = p
        self.part_lbl.setText(f"{p['name']} · stock {p['quantity']}")
        self.part_price.set_d(p["selling_price"])
        self.part_qty.setMaximum(max(1, p["quantity"]))
        self.part_qty.setValue(1)

    def add_part(self):
        if not self._part:
            error(self, "Search and select a spare part first.")
            return
        if run_safely(self, repairs.add_part, self.job_id, self._part["id"], self.part_qty.value(), self.part_price.value_d(),
                      success=f"{self._part['name']} added · stock deducted", topic="service"):
            self._part = None
            self.part_lbl.setText("")
            self.part_picker.reload()
            self.load()

    def remove_part(self):
        r = self.parts.selected()
        if not r or r["status"] != "ACTIVE":
            error(self, "Select a part that is in use on this job.")
            return
        if confirm(self, "Remove part", f"Remove {r['product_name']} from this job? It goes back to stock."):
            if run_safely(self, repairs.remove_part, self.job_id, r["id"], success="Part returned to stock", topic="service"):
                self.part_picker.reload()
                self.load()

    def save_details(self):
        data = dict(technician=self.tech.currentText(), problem=self.problem.toPlainText(),
                    diagnosis=self.diagnosis.toPlainText(), estimated_delivery=self.est.value(),
                    warranty_days=self.warranty.value())
        if self.labour.isEnabled():
            data.update(labour_charge=self.labour.value_d(), discount=self.discount.value_d())
        if run_safely(self, repairs.update_job, self.job_id, data, topic="service"):
            new_status = self.status.currentData()
            if new_status != self.j["status"]:
                self.set_status(new_status)
                return
            toast("Job saved")
            self.load()

    def set_status(self, status):
        if status == "CANCELLED":
            self.cancel_job()
            return
        if status == "DELIVERED":
            self.deliver()
            return
        if run_safely(self, repairs.set_status, self.job_id, status, success=f"Status: {STATUS_LABELS[status]}", topic="service"):
            self.load()

    def add_payment(self):
        from ..dialogs import PaymentDialog
        bal = self.j["total"] - self.j["paid"]
        dlg = PaymentDialog(self, f"Payment – {self.j['job_no']}", bal if bal > 0 else Decimal("999999"),
                            "Amount received")
        if bal <= 0:
            dlg.amount.setValue(0)
        if dlg.exec() == QDialog.Accepted:
            amt, mode, note = dlg.result
            if run_safely(self, repairs.add_job_payment, self.job_id, amt, mode, note, new_ref(),
                          success=f"Payment {inr(amt)} recorded", topic="service"):
                self.load()

    def deliver(self):
        self.save_details_silent()
        j = repairs.get_job(self.job_id)
        bal = j["total"] - j["paid"]
        dlg = FormDialog(self, f"Deliver {j['job_no']}", 440)
        dlg.form.addRow(label(f"Total {inr(j['total'])} · paid {inr(j['paid'])}", "SectionTitle"))
        amt = MoneySpin()
        amt.set_d(max(bal, 0))
        mode = ModeButtons()
        if bal < 0:
            dlg.form.addRow(label(f"Advance is more than the bill – {inr(-bal)} will be refunded.", "WarnText"))
        dlg.form.addRow("Collect now", amt)
        dlg.form.addRow("Mode", mode)
        dlg.form.addRow(label("Anything not collected stays as the customer's pending balance.", "Muted", wrap=True))
        dlg.ok_btn.setText("Deliver")
        dlg.finish_layout()
        dlg.on_save = lambda: setattr(dlg, "res", repairs.deliver_job(self.job_id, amt.value_d(), mode.value(), mode.value()))
        if dlg.exec() == QDialog.Accepted:
            changed("service")
            toast(f"{j['job_no']} delivered" + (f" · refund {inr(dlg.res['refund'])}" if dlg.res["refund"] else ""))
            self.load()
            if confirm(self, "Print service bill", "Print the service bill for the customer?", "Print", "Skip"):
                print_job(self, self.job_id, "RECEIPT")

    def save_details_silent(self):
        if self.j["status"] in ("DELIVERED", "CANCELLED"):
            return
        data = dict(technician=self.tech.currentText(), diagnosis=self.diagnosis.toPlainText())
        if self.labour.isEnabled():
            data.update(labour_charge=self.labour.value_d(), discount=self.discount.value_d())
        try:
            repairs.update_job(self.job_id, data)
        except Exception:
            pass

    def cancel_job(self):
        reason = ask_text(self, "Cancel job", "Reason for cancelling (parts go back to stock, advance is refunded):")
        if reason:
            if run_safely(self, repairs.set_status, self.job_id, "CANCELLED", reason=reason, refund_mode="CASH",
                          success="Job cancelled", topic="service"):
                self.load()
        else:
            self.load()


class ServicesPage(Page):
    subtitle = "Open repair jobs – click a job to add parts, update status, take payment or deliver"

    def __init__(self, parent=None):
        super().__init__(parent)
        top = QHBoxLayout()
        self.chips: dict[str, QPushButton] = {}
        short = {"RECEIVED": "Received", "INSPECTION": "Inspection", "WAITING_PARTS": "Waiting parts",
                 "IN_PROGRESS": "In progress", "READY": "Ready"}
        for key, text in [("OPEN", "All open")] + [(s, short[s]) for s in short] + [("ALL", "All jobs")]:
            b = QPushButton(text)
            b.setProperty("variant", "chip")
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, k=key: self._chip(k))
            self.chips[key] = b
            top.addWidget(b)
        top.addStretch(1)
        nb = button("New repair job (Ctrl+J)", "primary", "plus", self.new_job)
        nb.setMinimumWidth(210)
        top.addWidget(nb)
        self.root.addLayout(top)
        card = Card()
        self.table = DataTable([Col("job_no", "Job", width=95, bold=True), Col("received_date", "Received", "date", 100),
                                Col("customer_name", "Customer", width=150), Col("customer_phone", "Phone", width=110),
                                Col("device", "Device", width=150), Col("problem", "Problem", width=200),
                                Col("technician", "Technician", width=100),
                                Col("estimated_delivery", "Due", "date", 95, color=lambda r: "bad" if r["overdue"] else None),
                                Col("status", "Status", "status", 150), Col("total", "Total", "money", 95),
                                Col("balance", "Balance", "money", 95, color=lambda r: "warn" if r["balance"] > 0 else None)],
                               placeholder="Search job no, customer, phone, IMEI, model…", stretch="problem")
        self.table.activated.connect(lambda r: self.open_job(r["id"]))
        card.add(self.table)
        card.add(hbox(button("Open job", "primary", "wrench", lambda: self.open_job((self.table.selected() or {}).get("id"))),
                      button("Print job card", "", "printer",
                             lambda: (self.table.selected() and print_job(self, self.table.selected()["id"], "JOB_CARD"))),
                      None))
        self.root.addWidget(card, 1)
        self._filter = "OPEN"
        self.chips["OPEN"].setChecked(True)

    def _chip(self, key):
        self._filter = key
        for k, b in self.chips.items():
            b.setChecked(k == key)
        self.refresh()

    def refresh(self):
        counts = repairs.status_counts()
        for k, b in self.chips.items():
            base = b.text().split("  (")[0]
            n = sum(counts.get(s, 0) for s in OPEN_STATUSES) if k == "OPEN" else (sum(counts.values()) if k == "ALL" else counts.get(k, 0))
            b.setText(f"{base}  ({n})")
        statuses = None if self._filter == "ALL" else (list(OPEN_STATUSES) if self._filter == "OPEN" else [self._filter])
        rows = repairs.list_jobs(statuses=statuses)
        self.table.set_rows(rows)
        self.table.set_footer(f"{len(rows)} job(s) · pending balance {inr(sum((r['balance'] for r in rows), Decimal(0)))}")

    def open_with(self, payload):
        if isinstance(payload, dict):
            if payload.get("new"):
                self.new_job()
            elif payload.get("job_id"):
                self.open_job(payload["job_id"])

    def new_job(self):
        dlg = NewJobDialog(self)
        if dlg.exec() == QDialog.Accepted:
            toast(f"Job {dlg.res['job_no']} created")
            self.refresh()
            print_job(self, dlg.res["id"], "JOB_CARD")

    def open_job(self, job_id):
        if not job_id:
            error(self, "Select a job first.")
            return
        JobDialog(self, job_id).exec()
        self.refresh()
