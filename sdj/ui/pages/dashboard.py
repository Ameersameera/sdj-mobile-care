"""Dashboard: today's business, payments, stock, analytics, recent activity, quick actions."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import QComboBox, QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from ...core import reports
from ...core.auth import current_user
from ...output.fmt import d_only, inr, qty
from ..bus import go
from ..charts import ChartWidget
from ..widgets import Card, Col, DataTable, KpiCard, Page, button, label, money_tone, scroll_wrap


class DashboardPage(Page):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.root.setContentsMargins(0, 0, 0, 0)
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(22, 16, 22, 22)
        lay.setSpacing(16)
        self.root.addWidget(scroll_wrap(host))
        u = current_user()
        self.can_profit = u.can("view_profit")
        # greeting + quick actions
        top = QHBoxLayout()
        col = QVBoxLayout()
        col.setSpacing(2)
        self.greet = label("", "SectionTitle")
        self.greet.setStyleSheet("font-size: 14pt;")
        self.when = label("", "Muted")
        col.addWidget(self.greet)
        col.addWidget(self.when)
        top.addLayout(col, 1)
        self.backup_btn = button("", "ghost", "hard-drive", lambda: go("settings", {"tab": "backup"}))
        self.backup_btn.setToolTip("Where the latest backup is kept (phone / 2nd folder). Click for Settings → Backup.")
        self.show_backup = u.can("backup")
        self.backup_btn.setVisible(self.show_backup)
        top.addWidget(self.backup_btn)
        lay.addLayout(top)
        top = QHBoxLayout()
        top.setSpacing(8)
        actions = [("New bill", "receipt-indian-rupee", "billing", None, "billing"),
                   ("Quick sale", "zap", "quick_sale", None, "quick_sale"),
                   ("New repair job", "wrench", "services", {"new": True}, "services"),
                   ("Add product", "package-plus", "products", {"new": True}, "products_edit"),
                   ("Purchase", "shopping-cart", "purchases", None, "purchases"),
                   ("Expense", "wallet", "expenses", None, "expenses"),
                   ("Close day", "calendar-check", "closing", None, "daily_closing")]
        for text, ic, page, payload, perm in actions:
            if u.can(perm):
                b = button(text, "quick", ic, lambda pg=page, pl=payload: go(pg, pl))
                top.addWidget(b)
        top.addStretch(1)
        lay.addLayout(top)
        # today's business
        lay.addWidget(label("TODAY'S BUSINESS", "BigTotalLabel"))
        grid = QGridLayout()
        grid.setSpacing(12)
        self.k = {}
        specs = [("sales", "Total sales", "indian-rupee"), ("invoices", "Invoices", "receipt"),
                 ("products", "Products sold", "package"), ("jobs", "Service jobs", "wrench"),
                 ("service", "Service income", "smartphone"), ("spares", "Spare parts sold", "cpu"),
                 ("expenses", "Expenses", "wallet"), ("gp", "Gross profit", "trending-up"),
                 ("np", "Net operating profit", "badge-percent")]
        if not self.can_profit:
            specs = [s for s in specs if s[0] not in ("gp", "np")]
        for i, (key, title, ic) in enumerate(specs):
            card = KpiCard(title, ic)
            self.k[key] = card
            grid.addWidget(card, i // 5, i % 5)
        self.k["sales"].clicked.connect(lambda: go("live"))
        self.k["jobs"].clicked.connect(lambda: go("services"))
        lay.addLayout(grid)
        # payments + stock
        row2 = QVBoxLayout()
        row2.setSpacing(16)
        pay = Card("Payment summary", "today")
        pg = QGridLayout()
        pg.setSpacing(10)
        self.pk = {}
        for i, (key, title, ic) in enumerate([("CASH", "Cash received", "banknote"), ("UPI", "UPI received", "qr-code"),
                                              ("CARD", "Card payments", "credit-card"), ("OTHER", "Other payments", "wallet"),
                                              ("pending", "Customer pending", "hand-coins")]):
            c = KpiCard(title, ic, compact=True)
            self.pk[key] = c
            pg.addWidget(c, 0, i)
        self.pk["pending"].clicked.connect(lambda: go("customers", {"dues": True}))
        pay.add(pg)
        row2.addWidget(pay, 1)
        stock = Card("Stock summary")
        sg = QGridLayout()
        sg.setSpacing(10)
        self.sk = {}
        specs = [("products", "Total products", "boxes"), ("qty", "Total stock qty", "layers"),
                 ("value", "Stock value", "indian-rupee"), ("low", "Low stock", "triangle-alert"),
                 ("out", "Out of stock", "circle-alert"), ("sold", "Sold today", "trending-down")]
        if not self.can_profit:
            specs = [s for s in specs if s[0] != "value"]
        for i, (key, title, ic) in enumerate(specs):
            c = KpiCard(title, ic, compact=True)
            self.sk[key] = c
            sg.addWidget(c, 0, i)
        self.sk["low"].clicked.connect(lambda: go("stock", {"filter": "LOW"}))
        self.sk["out"].clicked.connect(lambda: go("stock", {"filter": "OUT"}))
        stock.add(sg)
        row2.addWidget(stock, 1)
        lay.addLayout(row2)
        # analytics
        row3 = QHBoxLayout()
        row3.setSpacing(16)
        c1 = Card("Sales analytics")
        self.sales_kind = QComboBox()
        self.sales_kind.addItems(["Daily (14 days)", "Weekly (8 weeks)", "Monthly (12 months)"])
        c1.header.addWidget(self.sales_kind)
        self.chart1 = ChartWidget(height=270)
        c1.add(self.chart1)
        row3.addWidget(c1, 1)
        c2 = Card("Business insights")
        self.insight_kind = QComboBox()
        items = ["Revenue vs expenses", "Top products (this month)", "Service income (14 days)"]
        if self.can_profit:
            items.append("Profit (12 months)")
        self.insight_kind.addItems(items)
        c2.header.addWidget(self.insight_kind)
        self.chart2 = ChartWidget(height=270)
        c2.add(self.chart2)
        row3.addWidget(c2, 1)
        lay.addLayout(row3)
        self.sales_kind.currentIndexChanged.connect(self._draw_charts)
        self.insight_kind.currentIndexChanged.connect(self._draw_charts)
        # recent
        rc = Card("Recent transactions")
        rc.header.addWidget(button("Sales history", "ghost", None, lambda: go("sales_history")))
        self.recent = DataTable([Col("time", "Time", "datetime", 160), Col("type", "Type", width=110),
                                 Col("ref", "Reference", width=150), Col("party", "Customer / details", width=260),
                                 Col("amount", "Amount", "money", 120), Col("status", "Status", width=110)],
                                search=False, stretch="party")
        self.recent.setMinimumHeight(330)
        rc.add(self.recent)
        lay.addWidget(rc)
        lay.addStretch(1)
        self._charts = None

    def refresh(self):
        d = reports.dashboard_data()
        f, c = d["fig"]["f"], d["fig"]["counts"]
        u = current_user()
        hour = datetime.now().hour
        part = "morning" if hour < 12 else ("afternoon" if hour < 17 else "evening")
        self.greet.setText(f"Good {part}, {u.display_name}")
        self.when.setText(f"Business date {d_only(d['business_date'])} · figures update automatically after every entry")
        self.k["sales"].set(inr(f["total_revenue"]), f"Counter {inr(f['net_counter_sales'])} + service {inr(f['service_income'])}")
        self.k["invoices"].set(qty(c.get("invoices", 0)), f"{c.get('quick_sales', 0)} quick sales")
        self.k["products"].set(qty(c.get("qty_sold", 0)), f"{qty(c.get('qty_returned', 0))} returned" if c.get("qty_returned") else "")
        self.k["jobs"].set(qty(c.get("jobs_received", 0)), f"{c.get('jobs_completed', 0)} completed · {c.get('jobs_pending', 0)} open")
        self.k["service"].set(inr(f["service_income"]), f"Labour {inr(f['labour'])}")
        self.k["spares"].set(qty(int(f["spare_parts_sold"])), "counter + used in repairs")
        self.k["expenses"].set(inr(f["expenses"]), f"{c.get('expense_entries', 0)} entries")
        if self.can_profit:
            self.k["gp"].set(inr(f["gross_profit"]), f"COGS {inr(f['cogs'])}", money_tone(f["gross_profit"]))
            self.k["np"].set(inr(f["net_operating_profit"]), "after labour & expenses", money_tone(f["net_operating_profit"]))
        pin = d["fig"]["pay_in"]
        for m in ("CASH", "UPI", "CARD", "OTHER"):
            self.pk[m].set(inr(pin[m]))
        self.pk["pending"].set(inr(d["receivables"]["total"]), f"{d['receivables']['count']} customers", "warn" if d["receivables"]["total"] > 0 else "")
        st = d["stock"]
        self.sk["products"].set(qty(st["total_products"]))
        self.sk["qty"].set(qty(st["total_quantity"]))
        if "value" in self.sk:
            self.sk["value"].set(inr(st["stock_value"]), "at cost")
        self.sk["low"].set(qty(st["low_stock"]), "click to view", "warn" if st["low_stock"] else "")
        self.sk["out"].set(qty(st["out_of_stock"]), "click to view", "bad" if st["out_of_stock"] else "")
        self.sk["sold"].set(qty(c.get("qty_sold", 0)))
        self.recent.set_rows(d["recent"])
        if self.show_backup:
            try:
                from ..offsite_ui import backup_line
                text, late = backup_line()
                self.backup_btn.setText(("⚠ " if late else "") + text)
                self.backup_btn.setProperty("variant", "danger" if late else "ghost")
                self.backup_btn.style().unpolish(self.backup_btn)
                self.backup_btn.style().polish(self.backup_btn)
            except Exception:  # the dashboard must always open
                import logging
                logging.getLogger(__name__).exception("backup line failed")
        self._charts = reports.chart_data()
        self._draw_charts()

    def _draw_charts(self):
        cd = self._charts
        if not cd:
            return
        k = self.sales_kind.currentIndex()
        key = ("daily", "weekly", "monthly")[k]
        title = ("Revenue per day", "Revenue per week (week starting)", "Revenue per month")[k]
        self.chart1.bars(cd[key]["labels"], cd[key]["revenue"], title, "Revenue")
        t = self.insight_kind.currentText()
        if t.startswith("Revenue vs"):
            self.chart2.grouped(cd["rev_exp"]["labels"], [("Revenue", cd["rev_exp"]["revenue"]),
                                                         ("Expenses", cd["rev_exp"]["expenses"])], "Last 6 months")
        elif t.startswith("Top products"):
            labels = cd["products"]["labels"] or []
            self.chart2.bars(labels, cd["products"]["values"], "Sales value excl. GST", "Sales", horizontal=True)
        elif t.startswith("Service"):
            self.chart2.stacked(cd["service"]["labels"], [("Labour", cd["service"]["labour"]),
                                                          ("Spare parts", cd["service"]["parts"])], "Service income per day")
        else:
            self.chart2.lines(cd["profit"]["labels"], [("Gross profit", cd["profit"]["gross"]),
                                                       ("Net operating profit", cd["profit"]["net"])], "Monthly profit")
