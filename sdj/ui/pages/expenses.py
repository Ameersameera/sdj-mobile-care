"""Daily expenses with categories, payment mode, edit / void (never deleted)."""
from __future__ import annotations

from decimal import Decimal

from PySide6.QtWidgets import QComboBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit

from ...core import expenses
from ...core.business_day import current_business_date
from ...db.base import session_scope
from ...output.fmt import inr
from ..bus import toast
from ..widgets import (Card, Col, DataTable, DateEdit, DateRangeBar, ModeButtons, MoneySpin, Page, ask_text, button,
                       confirm, error, hbox, label, run_safely)


class ExpensesPage(Page):
    subtitle = "Rent, electricity, salary, transport and other running costs"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.editing = None
        form = Card("Add expense")
        g = QGridLayout()
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(8)
        self.date = DateEdit()
        self.category = QComboBox()
        self.category.setEditable(True)
        self.category.addItems(expenses.categories())
        self.desc = QLineEdit()
        self.desc.setPlaceholderText("Description")
        self.amount = MoneySpin()
        self.mode = ModeButtons()
        self.notes = QLineEdit()
        self.notes.setPlaceholderText("Notes (optional)")
        g.addWidget(QLabel("Date"), 0, 0)
        g.addWidget(self.date, 0, 1)
        g.addWidget(QLabel("Category"), 0, 2)
        g.addWidget(self.category, 0, 3)
        g.addWidget(QLabel("Amount"), 0, 4)
        g.addWidget(self.amount, 0, 5)
        g.addWidget(QLabel("Description"), 1, 0)
        g.addWidget(self.desc, 1, 1, 1, 3)
        g.addWidget(QLabel("Paid by"), 1, 4)
        g.addWidget(self.mode, 1, 5)
        g.addWidget(QLabel("Notes"), 2, 0)
        g.addWidget(self.notes, 2, 1, 1, 3)
        self.save_btn = button("Save expense", "primary", "save", self.save)
        self.cancel_edit = button("Cancel edit", "ghost", None, self.reset)
        self.cancel_edit.setVisible(False)
        g.addLayout(hbox(None, self.cancel_edit, self.save_btn), 2, 4, 1, 2)
        form.add(g)
        self.root.addWidget(form)
        body = QHBoxLayout()
        body.setSpacing(14)
        lst = Card("Expenses")
        self.range = DateRangeBar("This month")
        self.range.changed.connect(lambda *_: self.refresh())
        self.table = DataTable([Col("business_date", "Date", "date", 100), Col("category", "Category", width=130, bold=True),
                                Col("description", "Description", width=220), Col("amount", "Amount", "money", 110),
                                Col("payment_mode", "Mode", width=70), Col("notes", "Notes", width=160),
                                Col("status", "Status", "status", 80)], placeholder="Search expenses…", stretch="description")
        self.table.add_top(self.range)
        self.table.activated.connect(self.edit)
        lst.add(self.table)
        lst.add(hbox(button("Edit", "", "pencil", lambda: self.edit(self.table.selected())),
                     button("Void", "danger", "x", self.void),
                     button("Export", "", "file-spreadsheet", lambda: self.table.export(self, "Expenses")), None))
        body.addWidget(lst, 3)
        cats = Card("By category")
        self.cat_table = DataTable([Col("category", "Category", width=140, bold=True), Col("amount", "Amount", "money", 110)],
                                   search=False, stretch="category")
        cats.add(self.cat_table)
        body.addWidget(cats, 1)
        self.root.addLayout(body, 1)

    def refresh(self):
        with session_scope() as s:
            if not self.editing:
                self.date.set_date(current_business_date(s))
        a, b = self.range.range()
        rows = expenses.list_expenses(a, b, include_void=True)
        self.table.set_rows(rows)
        act = [r for r in rows if r["status"] == "ACTIVE"]
        total = sum((r["amount"] for r in act), Decimal(0))
        self.table.set_footer(f"{len(act)} expenses · total {inr(total)}")
        by = {}
        for r in act:
            by[r["category"]] = by.get(r["category"], Decimal(0)) + r["amount"]
        cat_rows = [dict(category=k, amount=v) for k, v in sorted(by.items(), key=lambda kv: kv[1], reverse=True)]
        cat_rows.append(dict(category="TOTAL", amount=total))
        self.cat_table.set_rows(cat_rows)

    def save(self):
        args = dict(category=self.category.currentText(), amount=self.amount.value_d(), description=self.desc.text(),
                    payment_mode=self.mode.value(), notes=self.notes.text(), expense_date=self.date.value())
        if self.editing:
            ok = run_safely(self, expenses.update_expense, self.editing, **args, success="Expense updated", topic="expenses")
        else:
            ok = run_safely(self, expenses.add_expense, args.pop("category"), args.pop("amount"), **args,
                            success="Expense saved", topic="expenses")
        if ok:
            self.reset()

    def reset(self):
        self.editing = None
        self.amount.setValue(0)
        self.desc.clear()
        self.notes.clear()
        self.save_btn.setText("Save expense")
        self.cancel_edit.setVisible(False)
        self.refresh()

    def edit(self, r):
        if not r or r["status"] != "ACTIVE":
            return
        self.editing = r["id"]
        self.date.set_date(r["business_date"])
        self.category.setCurrentText(r["category"])
        self.amount.set_d(r["amount"])
        self.desc.setText(r["description"])
        self.notes.setText(r["notes"])
        self.mode.set(r["payment_mode"])
        self.save_btn.setText("Update expense")
        self.cancel_edit.setVisible(True)

    def void(self):
        r = self.table.selected()
        if not r or r["status"] != "ACTIVE":
            error(self, "Select an active expense.")
            return
        reason = ask_text(self, "Void expense", f"Why void the {r['category']} expense of {inr(r['amount'])}?")
        if reason:
            run_safely(self, expenses.void_expense, r["id"], reason, success="Expense voided", topic="expenses")
