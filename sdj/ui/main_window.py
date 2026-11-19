"""Main window: branded sidebar, header with business-day status, lazily created pages."""
from __future__ import annotations

import importlib
import logging
from datetime import date

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMenu,
                               QStackedWidget, QStatusBar, QToolButton, QVBoxLayout, QWidget)

from .. import APP_SHORT_TITLE, APP_VERSION
from ..core import auth, settings
from ..core.auth import ROLE_LABELS, current_user
from ..db.base import current_db_path, session_scope
from . import icons
from .bus import bus
from .login import logo_pixmap
from .theme import current, set_theme, stylesheet
from .widgets import label

log = logging.getLogger(__name__)

# key, title, icon, permission, module, class  – order exactly as in the specification
PAGES = [
    ("dashboard", "Dashboard", "layout-dashboard", "dashboard", "dashboard", "DashboardPage"),
    ("billing", "New Billing", "receipt-indian-rupee", "billing", "billing", "BillingPage"),
    ("quick_sale", "Quick Sale", "zap", "quick_sale", "quick_sale", "QuickSalePage"),
    ("sales_history", "Sales History", "history", "sales_history", "sales_history", "SalesHistoryPage"),
    ("products", "Products", "package", "products_view", "products", "ProductsPage"),
    ("stock", "Stock Management", "warehouse", "stock_view", "stock", "StockPage"),
    ("purchases", "Purchases", "shopping-cart", "purchases", "purchases", "PurchasesPage"),
    ("services", "Mobile Services", "wrench", "services", "services", "ServicesPage"),
    ("service_history", "Service History", "clipboard-list", "service_history", "service_history", "ServiceHistoryPage"),
    ("spare_parts", "Spare Parts", "cpu", "spare_parts", "spare_parts", "SparePartsPage"),
    ("customers", "Customers", "users", "customers", "customers", "CustomersPage"),
    ("suppliers", "Suppliers", "truck", "suppliers", "suppliers", "SuppliersPage"),
    ("expenses", "Expenses", "wallet", "expenses", "expenses", "ExpensesPage"),
    ("returns", "Returns & Exchanges", "arrow-left-right", "returns", "returns", "ReturnsPage"),
    ("live", "Live Business Summary", "activity", "live_summary", "live_summary", "LiveSummaryPage"),
    ("closing", "Daily Closing", "calendar-check", "daily_closing", "daily_closing", "DailyClosingPage"),
    ("monthly", "Monthly Reports", "chart-column", "reports", "reports_pages", "MonthlyReportsPage"),
    ("yearly", "Yearly Reports", "chart-line", "reports", "reports_pages", "YearlyReportsPage"),
    ("accounting", "Accounting", "calculator", "accounting", "accounting", "AccountingPage"),
    ("settings", "Settings", "settings", None, "settings_page", "SettingsPage"),
]


class MainWindow(QMainWindow):
    logout_requested = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{settings.get('shop_name') or 'SDJ Mobile Care'} – Smart Shop Management System")
        self.setMinimumSize(1200, 720)
        self.pages: dict[str, QWidget] = {}
        self.page_meta = {}
        user = current_user()
        self.allowed = [p for p in PAGES if p[3] is None or user.can(p[3])]
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        rl.addWidget(self._build_header())
        self.stack = QStackedWidget()
        rl.addWidget(self.stack, 1)
        root.addWidget(right, 1)
        self._build_status()
        self.toast_lbl = QLabel(central)
        self.toast_lbl.setObjectName("Toast")
        self.toast_lbl.hide()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self.toast_lbl.hide)
        b = bus()
        b.data_changed.connect(self._on_data_changed)
        b.navigate.connect(self.navigate)
        b.toast.connect(self.show_toast)
        b.settings_changed.connect(self._on_settings_changed)
        self._shortcuts()
        self._header_timer = QTimer(self)
        self._header_timer.timeout.connect(self.update_header)
        self._header_timer.start(60_000)
        self.navigate("dashboard" if user.can("dashboard") else self.allowed[0][0])
        self.update_header()

    # ------------------------------------------------------------ layout
    def _build_sidebar(self) -> QFrame:
        p = current()
        side = QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(236)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(0, 14, 0, 12)
        lay.setSpacing(6)
        brand = QHBoxLayout()
        brand.setContentsMargins(16, 0, 12, 4)
        self.side_logo = QLabel()
        self.side_logo.setPixmap(logo_pixmap(48))
        brand.addWidget(self.side_logo)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.brand_name = QLabel(settings.get("shop_name") or "SDJ Mobile Care")
        self.brand_name.setObjectName("BrandName")
        self.brand_name.setWordWrap(True)
        sub = QLabel("SMART SHOP SYSTEM")
        sub.setObjectName("BrandSub")
        col.addWidget(self.brand_name)
        col.addWidget(sub)
        brand.addLayout(col, 1)
        lay.addLayout(brand)
        self.side = QListWidget()
        self.side.setObjectName("SideList")
        self.side.setIconSize(QSize(18, 18))
        self.side.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.side.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        for key, title, icon_name, *_ in self.allowed:
            it = QListWidgetItem(icons.icon(icon_name, p.sidebar_text, 18), title)
            it.setData(Qt.UserRole, key)
            it.setSizeHint(QSize(200, 36))
            self.side.addItem(it)
        self.side.currentItemChanged.connect(self._side_changed)
        lay.addWidget(self.side, 1)
        user = current_user()
        foot = QHBoxLayout()
        foot.setContentsMargins(16, 4, 12, 0)
        uc = QVBoxLayout()
        uc.setSpacing(0)
        u1 = QLabel(user.display_name)
        u1.setObjectName("SideUser")
        u2 = QLabel(ROLE_LABELS.get(user.role, user.role))
        u2.setObjectName("SideRole")
        uc.addWidget(u1)
        uc.addWidget(u2)
        foot.addLayout(uc, 1)
        out = QToolButton()
        out.setObjectName("HeaderBtn")
        out.setIcon(icons.icon("log-out", p.sidebar_text, 18))
        out.setToolTip("Log out (Ctrl+Shift+L)")
        out.clicked.connect(self.logout)
        foot.addWidget(out)
        lay.addLayout(foot)
        return side

    def _build_header(self) -> QFrame:
        p = current()
        head = QFrame()
        head.setObjectName("Header")
        head.setFixedHeight(66)
        lay = QHBoxLayout(head)
        lay.setContentsMargins(22, 8, 18, 8)
        lay.setSpacing(10)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.title_lbl = label("", "PageTitle")
        self.sub_lbl = label("", "PageSub")
        col.addWidget(self.title_lbl)
        col.addWidget(self.sub_lbl)
        lay.addLayout(col, 1)
        self.alert_pill = QToolButton()
        self.alert_pill.setObjectName("AlertPill")
        self.alert_pill.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.alert_pill.setCursor(Qt.PointingHandCursor)
        self.alert_pill.clicked.connect(lambda: self.navigate("stock", {"filter": "ALERT"}))
        lay.addWidget(self.alert_pill)
        self.phone_pill = QToolButton()
        self.phone_pill.setObjectName("AlertPill")
        self.phone_pill.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.phone_pill.setCursor(Qt.PointingHandCursor)
        self.phone_pill.setToolTip("Entries from a phone that need your check")
        self.phone_pill.clicked.connect(self.show_sync_alerts)
        self.phone_pill.hide()
        lay.addWidget(self.phone_pill)
        self.day_pill = QLabel()
        self.day_pill.setObjectName("DayPill")
        lay.addWidget(self.day_pill)
        for icon_name, tip, slot in (("keyboard", "Keyboard shortcuts (F1)", self.show_shortcuts),
                                     ("moon" if p.name == "light" else "sun", "Switch light / dark theme", self.toggle_theme)):
            b = QToolButton()
            b.setObjectName("HeaderBtn")
            b.setIcon(icons.icon(icon_name, p.text2, 18))
            b.setToolTip(tip)
            b.clicked.connect(slot)
            lay.addWidget(b)
        self.user_btn = QToolButton()
        self.user_btn.setObjectName("HeaderBtn")
        self.user_btn.setIcon(icons.icon("user", p.text2, 18))
        self.user_btn.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self.user_btn)
        menu.addAction("My account / change password", lambda: self.navigate("settings", {"tab": "account"}))
        menu.addAction("Log out", self.logout)
        menu.addSeparator()
        menu.addAction("Exit", self.close)
        self.user_btn.setMenu(menu)
        lay.addWidget(self.user_btn)
        return head

    def _build_status(self):
        sb = QStatusBar()
        self.setStatusBar(sb)
        self.status_db = QLabel()
        self.status_backup = QLabel()
        sb.addWidget(QLabel("● Offline-ready"))
        sb.addWidget(self.status_db)
        sb.addPermanentWidget(self.status_backup)
        sb.addPermanentWidget(QLabel(f"v{APP_VERSION}"))

    def _shortcuts(self):
        for seq, page in (("Ctrl+D", "dashboard"), ("Ctrl+B", "billing"), ("F2", "billing"), ("Ctrl+K", "quick_sale"),
                          ("Ctrl+P", "products"), ("Ctrl+E", "expenses"), ("Ctrl+L", "live")):
            sc = QShortcut(QKeySequence(seq), self)
            sc.activated.connect(lambda pg=page: self.navigate(pg))
        QShortcut(QKeySequence("Ctrl+J"), self).activated.connect(lambda: self.navigate("services", {"new": True}))
        QShortcut(QKeySequence("F5"), self).activated.connect(self.refresh_current)
        QShortcut(QKeySequence("F1"), self).activated.connect(self.show_shortcuts)
        QShortcut(QKeySequence("Ctrl+Shift+L"), self).activated.connect(self.logout)

    # ------------------------------------------------------------ navigation
    def _side_changed(self, cur, _prev):
        if cur is not None:
            self._show_page(cur.data(Qt.UserRole))

    def navigate(self, key: str, payload=None):
        meta = next((p for p in self.allowed if p[0] == key), None)
        if meta is None:
            self.show_toast("You do not have access to that screen.", "error")
            return
        for i in range(self.side.count()):
            if self.side.item(i).data(Qt.UserRole) == key:
                if self.side.currentRow() != i:
                    self.side.blockSignals(True)
                    self.side.setCurrentRow(i)
                    self.side.blockSignals(False)
                break
        page = self._show_page(key)
        if payload is not None and page is not None and hasattr(page, "open_with"):
            QTimer.singleShot(0, lambda: page.open_with(payload))

    def _show_page(self, key: str):
        meta = next((p for p in self.allowed if p[0] == key), None)
        if meta is None:
            return None
        page = self.pages.get(key)
        if page is None:
            try:
                mod = importlib.import_module(f"sdj.ui.pages.{meta[4]}")
                page = getattr(mod, meta[5])(self)
            except Exception:
                log.exception("could not open page %s", key)
                from .widgets import error
                error(self, f"The '{meta[1]}' screen could not be opened. Details were written to the log file.")
                return None
            self.pages[key] = page
            self.stack.addWidget(page)
        self.stack.setCurrentWidget(page)
        self.current_key = key
        self.title_lbl.setText(meta[1])
        self.sub_lbl.setText(getattr(page, "subtitle", "") or date.today().strftime("%A, %d %B %Y"))
        return page

    def refresh_current(self):
        page = self.stack.currentWidget()
        if page is not None and hasattr(page, "refresh"):
            page.refresh()
            self.show_toast("Refreshed")

    # ------------------------------------------------------------ live updates
    def _on_data_changed(self, topic: str):
        for page in self.pages.values():
            if hasattr(page, "mark_dirty"):
                page.mark_dirty()
        self.update_header()

    def update_header(self):
        from ..core.business_day import current_business_date, is_day_closed
        from ..core.inventory import stock_summary
        try:
            with session_scope() as s:
                bdate = current_business_date(s)
                today_closed = is_day_closed(s, date.today())
            st = stock_summary()
        except Exception:
            log.exception("header update failed")
            return
        p = current()
        if today_closed:
            self.day_pill.setText(f"Today closed · new entries go to {bdate.strftime('%d %b')}")
            self.day_pill.setProperty("state", "closed")
        else:
            self.day_pill.setText(f"Business day: {bdate.strftime('%d %b %Y')} · Open")
            self.day_pill.setProperty("state", "open")
        self.day_pill.style().unpolish(self.day_pill)
        self.day_pill.style().polish(self.day_pill)
        try:
            from ..core.auth import current_user
            from ..core.phonesync import open_alert_count
            k = open_alert_count() if current_user().can("settings") else 0
        except Exception:
            k = 0
        self.phone_pill.setVisible(k > 0)
        self.phone_pill.setText(f"{k} phone alert{'s' if k != 1 else ''}")
        self.phone_pill.setIcon(icons.icon("smartphone", p.warn, 16))
        n = st["low_stock"] + st["out_of_stock"]
        self.alert_pill.setVisible(n > 0)
        self.alert_pill.setText(f"{n} stock alert{'s' if n != 1 else ''}")
        self.alert_pill.setIcon(icons.icon("triangle-alert", p.bad, 16))
        db = current_db_path()
        self.status_db.setText(f"Data: {db}" if db else "")
        last = settings.get("last_backup_at") or ""
        self.status_backup.setText(f"Last backup: {last.replace('T', ' ')[:16]}" if last else "No backup yet")

    def show_sync_alerts(self):
        from .phone_link import SyncAlertsDialog
        SyncAlertsDialog(self).exec()
        self.update_header()

    def _on_settings_changed(self):
        self.brand_name.setText(settings.get("shop_name") or "SDJ Mobile Care")
        self.side_logo.setPixmap(logo_pixmap(48))
        self.update_header()

    # ------------------------------------------------------------ toast / theme / misc
    def show_toast(self, message: str, kind: str = "info"):
        self.toast_lbl.setText(message)
        self.toast_lbl.setProperty("kind", kind)
        self.toast_lbl.style().unpolish(self.toast_lbl)
        self.toast_lbl.style().polish(self.toast_lbl)
        self.toast_lbl.adjustSize()
        cw = self.centralWidget()
        self.toast_lbl.move(cw.width() - self.toast_lbl.width() - 26, cw.height() - self.toast_lbl.height() - 26)
        self.toast_lbl.raise_()
        self.toast_lbl.show()
        self._toast_timer.start(3200 if kind == "info" else 6000)

    def toggle_theme(self):
        new = "dark" if current().name == "light" else "light"
        settings.set_value("theme", new)
        set_theme(new)
        from PySide6.QtWidgets import QApplication
        QApplication.instance().setStyleSheet(stylesheet())
        self.show_toast("Theme changes fully apply after the next sign-in.")
        bus().theme_changed.emit(new)
        for page in self.pages.values():
            if hasattr(page, "mark_dirty"):
                page.mark_dirty()

    def show_shortcuts(self):
        from .dialogs import ShortcutsDialog
        ShortcutsDialog(self).exec()

    def logout(self):
        from .widgets import confirm
        if not confirm(self, "Log out", "Log out of SDJ Mobile Care?", "Log out"):
            return
        auth.set_current_user(None)
        self.logout_requested.emit()

    def closeEvent(self, e):  # noqa: N802
        self._header_timer.stop()
        super().closeEvent(e)
