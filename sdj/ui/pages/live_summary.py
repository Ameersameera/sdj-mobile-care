"""Live business summary for the open business day – updates instantly, no need to close the day."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from ...core import inventory, parties, reports
from ...core.auth import current_user
from ...core.business_day import current_business_date
from ...db.base import session_scope
from ...output.fmt import d_only, inr, qty
from ..widgets import Card, Col, DataTable, KpiCard, Page, label, money_tone, scroll_wrap


class LiveSummaryPage(Page):
    subtitle = "Everything happening today, updated live"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.root.setContentsMargins(0, 0, 0, 0)
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(22, 16, 22, 22)
        lay.setSpacing(14)
        self.root.addWidget(scroll_wrap(host))
        self.can_profit = current_user().can("view_profit")
        self.status = label("", "Banner")
        lay.addWidget(self.status)
        grid = QGridLayout()
        grid.setSpacing(10)
        self.k = {}
        specs = [("revenue", "Total revenue", "indian-rupee"), ("sold", "Products sold", "package"),
                 ("mobile", "Mobile sales", "smartphone"), ("acc", "Accessories sales", "boxes"),
                 ("spare", "Spare parts sales", "cpu"), ("service", "Service income", "wrench"),
                 ("expenses", "Total expenses", "wallet"), ("gp", "Gross profit", "trending-up"),
                 ("np", "Net operating profit", "badge-percent"), ("cash", "Cash collection", "banknote"),
                 ("upi", "UPI collection", "qr-code"), ("card", "Card collection", "credit-card"),
                 ("pending", "Customer pending", "hand-coins"), ("stock", "Units in stock", "layers")]
        if not self.can_profit:
            specs = [s for s in specs if s[0] not in ("gp", "np")]
        for i, (key, title, ic) in enumerate(specs):
            c = KpiCard(title, ic, compact=True)
            self.k[key] = c
            grid.addWidget(c, i // 5, i % 5)
        lay.addLayout(grid)
        row = QHBoxLayout()
        row.setSpacing(14)
        pw = Card("Product-wise sales today")
        cols = [Col("name", "Product", width=220, bold=True), Col("sold", "Sold", "int", 60),
                Col("service", "Repairs", "int", 70), Col("sales_value", "Sales amount", "money", 115)]
        if self.can_profit:
            cols.append(Col("gross_profit", "Gross profit", "money", 110))
        cols.append(Col("closing", "Left", "int", 60))
        self.pw = DataTable(cols, search=False, stretch="name")
        self.pw.setMinimumHeight(300)
        pw.add(self.pw)
        row.addWidget(pw, 3)
        dues = Card("Customer pending balances")
        self.dues = DataTable([Col("name", "Customer", width=150, bold=True), Col("phone", "Phone", width=110),
                               Col("pending", "Pending", "money", 105)], search=False, stretch="name")
        self.dues.setMinimumHeight(300)
        dues.add(self.dues)
        row.addWidget(dues, 2)
        lay.addLayout(row)
        stock = Card("Remaining stock")
        self.stock = DataTable([Col("code", "ID", width=70), Col("name", "Product", width=240, bold=True),
                                Col("category", "Category", width=140),
                                Col("quantity", "Remaining", "int", 90, color=lambda r: "bad" if r["status"] == "OUT" else ("warn" if r["status"] == "LOW" else None)),
                                Col("min_stock", "Min", "int", 60), Col("status", "Status", "status", 90)],
                               placeholder="Search stock…", stretch="name")
        self.stock.setMinimumHeight(340)
        stock.add(self.stock)
        lay.addWidget(stock)
        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda: self.isVisible() and self.refresh())
        self.timer.start(60_000)

    def refresh(self):
        with session_scope() as s:
            d = current_business_date(s)
        fig = reports.period_figures(d, d)
        f, c, bg = fig["f"], fig["counts"], fig["by_group"]
        self.status.setText(f"<b>LIVE</b> · business date {d_only(d)} · last updated {datetime.now():%I:%M:%S %p} · "
                            "the day stays open until you press Close Day")
        self.k["revenue"].set(inr(f["total_revenue"]), f"{c.get('invoices', 0)} invoices · {c.get('jobs_completed', 0)} repairs")
        self.k["sold"].set(qty(c.get("qty_sold", 0)))
        self.k["mobile"].set(inr(bg["MOBILE"]["net"]), f"{bg['MOBILE']['qty']} units")
        self.k["acc"].set(inr(bg["ACCESSORY"]["net"]), f"{bg['ACCESSORY']['qty']} units")
        self.k["spare"].set(inr(bg["SPARE"]["net"] + f["parts_revenue"]), "counter + used in repairs")
        self.k["service"].set(inr(f["service_income"]), f"labour {inr(f['labour'])}")
        self.k["expenses"].set(inr(f["expenses"]))
        if self.can_profit:
            self.k["gp"].set(inr(f["gross_profit"]), "", money_tone(f["gross_profit"]))
            self.k["np"].set(inr(f["net_operating_profit"]), "", money_tone(f["net_operating_profit"]))
        self.k["cash"].set(inr(fig["pay_in"]["CASH"]))
        self.k["upi"].set(inr(fig["pay_in"]["UPI"]))
        self.k["card"].set(inr(fig["pay_in"]["CARD"]))
        rec = parties.total_receivables()
        self.k["pending"].set(inr(rec["total"]), f"{rec['count']} customers", "warn" if rec["total"] > 0 else "")
        st = inventory.stock_summary()
        self.k["stock"].set(qty(st["total_quantity"]), f"{st['low_stock']} low · {st['out_of_stock']} out")
        rows = [r for r in reports.product_wise(d, d, fig) if r["sold"] or r["service"] or r["sales_value"]]
        rows.sort(key=lambda r: r["sales_value"], reverse=True)
        self.pw.set_rows(rows)
        self.pw.set_footer(f"{len(rows)} products moved today · {inr(sum((r['sales_value'] for r in rows), Decimal(0)))} (excl. GST)")
        self.dues.set_rows(sorted(parties.list_customers(with_dues_only=True, include_walk_in=True),
                                  key=lambda r: r["pending"], reverse=True))
        self.stock.set_rows(inventory.list_products())
