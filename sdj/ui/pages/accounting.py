"""Accounting: P&L, GST summary, receivables (ageing), payables, cash & bank book, stock valuation."""
from __future__ import annotations

from PySide6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from ... import config
from ...core import reports
from ..widgets import Card, DateRangeBar, Page, ReportView, button, hbox, label
from .reports_pages import _ReportPage

TABS = [("Profit & loss", "pnl", True), ("GST summary", "gst", True), ("Customer dues", "recv", False),
        ("Supplier dues", "pay", False), ("Cash & bank book", "cash", True), ("Stock valuation", "stock", False)]


class AccountingPage(_ReportPage):
    subtitle = "Sales revenue, collections, customer credit, supplier dues and GST are kept separate"

    def __init__(self, parent=None):
        super().__init__(parent)
        bar = Card()
        self.range = DateRangeBar("This month")
        self.range.changed.connect(lambda *_: self.refresh())
        bar.add(hbox(self.range, None, *self._export_bar()))
        bar.add(label("Net operating profit = Gross profit + service labour − operating expenses. "
                      "Gross profit = Net sales (incl. spare parts used in repairs) − cost of goods sold (weighted average).",
                      "Muted", wrap=True))
        self.root.addWidget(bar)
        self.tabs = QTabWidget()
        self.views = {}
        for title, key, _ in TABS:
            v = ReportView()
            self.views[key] = v
            self.tabs.addTab(v, title.replace("&", "&&"))
        self.tabs.currentChanged.connect(lambda *_: self.refresh())
        self.root.addWidget(self.tabs, 1)

    def refresh(self):
        title, key, ranged = TABS[self.tabs.currentIndex()]
        a, b = self.range.range()
        self.range.setEnabled(ranged)
        rep = {"pnl": lambda: reports.build_pnl(a, b), "gst": lambda: reports.build_gst_summary(a, b),
               "recv": reports.build_receivables, "pay": reports.build_payables,
               "cash": lambda: reports.build_cashbook(a, b), "stock": reports.build_stock_valuation}[key]()
        self.report = rep
        self.file_stem = f"{rep.title.replace(' ', '_').replace('&', 'and')}_{a}_{b}" if ranged else rep.title.replace(" ", "_")
        self.views[key].set_report(rep)
