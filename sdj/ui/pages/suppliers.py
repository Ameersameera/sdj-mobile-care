"""Suppliers: contacts, purchases, payments and dues (supplier ledger)."""
from __future__ import annotations

from decimal import Decimal

from PySide6.QtWidgets import QDialog, QVBoxLayout

from ...core import parties
from ...output.fmt import inr
from ..dialogs import PaymentDialog, SupplierDialog
from ..widgets import Card, Col, DataTable, KpiCard, Page, button, error, hbox, label, new_ref, run_safely


class SuppliersPage(Page):
    subtitle = "Who you buy from and how much you owe them"

    def __init__(self, parent=None):
        super().__init__(parent)
        row = hbox()
        self.k_n = KpiCard("Suppliers", "truck", compact=True)
        self.k_p = KpiCard("Total purchases", "shopping-cart", compact=True)
        self.k_d = KpiCard("Total due to suppliers", "hand-coins", compact=True)
        for k in (self.k_n, self.k_p, self.k_d):
            row.addWidget(k)
        self.root.addLayout(row)
        card = Card()
        self.table = DataTable([Col("name", "Supplier", width=210, bold=True), Col("contact_person", "Contact", width=130),
                                Col("phone", "Phone", width=120), Col("gstin", "GSTIN", width=150),
                                Col("purchase_count", "Purchases", "int", 85), Col("total_purchases", "Purchased", "money", 120),
                                Col("paid", "Paid", "money", 110),
                                Col("due", "Due", "money", 110, color=lambda r: "warn" if r["due"] > 0 else None),
                                Col("last_purchase", "Last purchase", "date", 105)],
                               placeholder="Search suppliers…", stretch="name")
        self.table.activated.connect(self.ledger)
        card.add(self.table)
        card.add(hbox(button("Add supplier", "primary", "user-plus", self.add),
                      button("Edit", "", "pencil", self.edit), button("Ledger", "", "book-open", lambda: self.ledger(self.table.selected())),
                      button("Pay supplier", "", "hand-coins", self.pay), None))
        self.root.addWidget(card, 1)

    def refresh(self):
        rows = parties.list_suppliers()
        self.table.set_rows(rows)
        self.k_n.set(str(len(rows)))
        self.k_p.set(inr(sum((r["total_purchases"] for r in rows), Decimal(0))))
        due = sum((max(r["due"], Decimal(0)) for r in rows), Decimal(0))
        self.k_d.set(inr(due), "", "warn" if due > 0 else "")

    def add(self):
        if SupplierDialog(self).exec() == QDialog.Accepted:
            self.refresh()

    def edit(self):
        r = self.table.selected()
        if r and SupplierDialog(self, r).exec() == QDialog.Accepted:
            self.refresh()

    def pay(self):
        r = self.table.selected()
        if not r or r["due"] <= 0:
            error(self, "Select a supplier with an amount due.")
            return
        dlg = PaymentDialog(self, f"Pay {r['name']}", r["due"], "Amount paid")
        if dlg.exec() == QDialog.Accepted:
            amt, mode, note = dlg.result
            if run_safely(self, parties.pay_supplier, r["id"], amt, mode, note, client_ref=new_ref(),
                          success=f"Paid {inr(amt)} to {r['name']}", topic="purchases"):
                self.refresh()

    def ledger(self, r):
        if not r:
            return
        led = parties.supplier_ledger(r["id"])
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Supplier ledger – {r['name']}")
        dlg.resize(860, 520)
        lay = QVBoxLayout(dlg)
        lay.addWidget(label(f"{r['name']} · balance due {inr(led['balance'])}", "SectionTitle"))
        t = DataTable([Col("date", "Date", "date", 100), Col("particulars", "Particulars", width=230), Col("ref", "Reference", width=140),
                       Col("debit", "Paid (Dr)", "money", 110), Col("credit", "Purchased (Cr)", "money", 120),
                       Col("balance", "Balance", "money", 110, bold=True)], search=False, stretch="particulars")
        t.set_rows(led["rows"])
        lay.addWidget(t)
        dlg.exec()
