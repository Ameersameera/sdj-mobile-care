"""Login screen and first-run setup (shop details + admin account)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QCheckBox, QDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

from .. import APP_SHORT_TITLE, APP_VERSION, config
from ..core import auth, settings
from ..core.errors import BusinessError
from .theme import current
from .widgets import button, error, label, multiline


def logo_pixmap(size: int) -> QPixmap:
    custom = settings.get("shop_logo_path") or ""
    from pathlib import Path
    p = Path(custom) if custom and Path(custom).exists() else config.asset_path("logo.png")
    pm = QPixmap(str(p))
    return pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def _brand_panel(width: int = 330) -> QFrame:
    p = current()
    panel = QFrame()
    panel.setObjectName("Sidebar")
    panel.setFixedWidth(width)
    lay = QVBoxLayout(panel)
    lay.setContentsMargins(28, 34, 28, 28)
    lay.setSpacing(10)
    logo = QLabel()
    logo.setPixmap(logo_pixmap(170))
    logo.setAlignment(Qt.AlignCenter)
    lay.addWidget(logo)
    name = QLabel(settings.get("shop_name") or "SDJ Mobile Care")
    name.setObjectName("BrandName")
    name.setStyleSheet(f"font-size: 19pt; font-weight: 800; color: {p.accent};")
    name.setAlignment(Qt.AlignCenter)
    lay.addWidget(name)
    sub = QLabel("SMART SHOP MANAGEMENT SYSTEM")
    sub.setObjectName("BrandSub")
    sub.setAlignment(Qt.AlignCenter)
    lay.addWidget(sub)
    lay.addSpacing(18)
    for t in ("Sales & billing with automatic stock", "Mobile repair & service jobs", "Daily closing & cash reconciliation",
              "Monthly / yearly profit reports"):
        row = QLabel(f"•  {t}")
        row.setStyleSheet(f"color: {p.sidebar_text}; font-size: 9.5pt;")
        lay.addWidget(row)
    lay.addStretch(1)
    ver = QLabel(f"Version {APP_VERSION} · works fully offline")
    ver.setStyleSheet(f"color: {p.sidebar_muted}; font-size: 8pt;")
    ver.setAlignment(Qt.AlignCenter)
    lay.addWidget(ver)
    return panel


class LoginDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Sign in – {APP_SHORT_TITLE}")
        self.setFixedSize(800, 500)
        self.user = None
        self._fails = 0
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(_brand_panel())
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(56, 60, 56, 40)
        rl.setSpacing(10)
        rl.addWidget(label("Welcome back", "PageTitle"))
        rl.addWidget(label("Sign in to continue to your shop.", "Muted"))
        rl.addSpacing(18)
        rl.addWidget(label("Username"))
        self.username = QLineEdit()
        self.username.setObjectName("BigInput")
        self.username.setPlaceholderText("e.g. admin")
        rl.addWidget(self.username)
        rl.addWidget(label("Password"))
        self.password = QLineEdit()
        self.password.setObjectName("BigInput")
        self.password.setEchoMode(QLineEdit.Password)
        rl.addWidget(self.password)
        self.err = label("", "BadText", wrap=True)
        rl.addWidget(self.err)
        self.btn = button("Sign in", "primary", None, self._login)
        self.btn.setMinimumHeight(40)
        rl.addWidget(self.btn)
        rl.addSpacing(8)
        rl.addWidget(label("Forgot your password? The admin can reset it in Settings → Users.", "Muted", wrap=True))
        rl.addStretch(1)
        lay.addWidget(right, 1)
        self.username.returnPressed.connect(self.password.setFocus)
        self.password.returnPressed.connect(self._login)
        self.username.setFocus()

    def _login(self):
        try:
            self.user = auth.authenticate(self.username.text(), self.password.text())
        except BusinessError as exc:
            self._fails += 1
            self.err.setText(str(exc) + (" Too many attempts – please wait a moment." if self._fails >= 5 else ""))
            self.password.selectAll()
            self.password.setFocus()
            if self._fails >= 5:
                from PySide6.QtCore import QTimer
                self.btn.setEnabled(False)
                QTimer.singleShot(15000, lambda: (self.btn.setEnabled(True), self.err.setText("")))
                self._fails = 0
            return
        auth.set_current_user(self.user)
        self.accept()


class _Choice(QPushButton):
    """A big button with a title and a line of explanation (first screen of a new PC)."""

    def __init__(self, title: str, text: str, slot, primary: bool = False):
        super().__init__()
        self.setObjectName("StartChoice")
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName(title)
        p = current()
        self.setStyleSheet(f"QPushButton#StartChoice {{ border-radius: 10px; background: {p.card}; min-height: 66px; "
                           f"padding: 0px; "
                           f"border: {'2px' if primary else '1px'} solid {p.accent if primary else p.border}; }} "
                           f"QPushButton#StartChoice:hover, QPushButton#StartChoice:focus {{ border: 2px solid {p.accent}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(2)
        t = QLabel(title)
        t.setStyleSheet(f"font-size: 11.5pt; font-weight: 700; color: {p.text}; background: transparent; border: none;")
        d = QLabel(text)
        d.setWordWrap(True)
        d.setStyleSheet(f"font-size: 9.5pt; color: {p.text2}; background: transparent; border: none;")
        for w in (t, d):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
            lay.addWidget(w)
        self.setMinimumHeight(78)
        self.clicked.connect(slot)


class SetupWizard(QDialog):
    """First run: restore the shop from a phone / backup file, or start fresh with shop details (nothing invented –
    leave blank if unknown) and the admin account."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Welcome – {APP_SHORT_TITLE}")
        self.setFixedSize(900, 620)
        self.user = None
        self.restored = False
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(_brand_panel(300))
        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        start = QWidget()
        sl = QVBoxLayout(start)
        sl.setContentsMargins(36, 36, 36, 26)
        sl.setSpacing(12)
        sl.addWidget(label("Welcome to SDJ Mobile Care", "PageTitle"))
        sl.addWidget(label("Is this a new PC for an existing shop? Bring the shop's data back first. Otherwise start "
                           "fresh.", "Muted", wrap=True))
        sl.addSpacing(6)
        sl.addWidget(_Choice("Restore from phone", "The phone app keeps a backup after every daily closing – send it "
                             "here over Wi-Fi.", self.restore_phone, primary=True))
        sl.addWidget(_Choice("Restore from a backup file", "From a pen drive, or a backup downloaded from Google Drive "
                             "(.sdjb file).", self.restore_file))
        sl.addWidget(_Choice("Start fresh", "A new shop: enter the shop details and create the owner login.",
                             lambda: self.stack.setCurrentIndex(1)))
        sl.addStretch(1)
        sl.addWidget(label("No backup anywhere? Choose 'Restore from phone' – the phone can also rebuild the shop from "
                           "its own copy (products, stock, IMEIs, customers with dues, open repair jobs).", "Muted",
                           wrap=True))
        self.stack.addWidget(start)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(36, 28, 36, 22)
        rl.setSpacing(8)
        back = QPushButton("← Back")
        back.setFlat(True)
        back.setCursor(Qt.PointingHandCursor)
        back.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        rl.addWidget(back, 0, Qt.AlignLeft)
        rl.addWidget(label("Let's set up your shop", "PageTitle"))
        rl.addWidget(label("You can change all of this later in Settings. Leave a field blank if it does not apply.",
                           "Muted", wrap=True))
        form = QFormLayout()
        form.setVerticalSpacing(8)
        self.shop = QLineEdit(settings.get("shop_name") or "SDJ Mobile Care")
        self.address = multiline("Shop address (shown on bills)", 54)
        self.phone = QLineEdit()
        self.phone.setPlaceholderText("Shop phone number")
        self.gstin = QLineEdit()
        self.gstin.setPlaceholderText("Only if the shop is GST registered")
        self.gst = QCheckBox("Charge GST on bills (shop is GST registered)")
        form.addRow("Shop name", self.shop)
        form.addRow("Address", self.address)
        form.addRow("Phone", self.phone)
        form.addRow("GSTIN", self.gstin)
        form.addRow("", self.gst)
        rl.addLayout(form)
        rl.addSpacing(6)
        rl.addWidget(label("Owner / admin login", "SectionTitle"))
        f2 = QFormLayout()
        f2.setVerticalSpacing(8)
        self.full = QLineEdit()
        self.full.setPlaceholderText("Your name")
        self.username = QLineEdit("admin")
        self.pw1 = QLineEdit()
        self.pw1.setEchoMode(QLineEdit.Password)
        self.pw1.setPlaceholderText("At least 6 characters")
        self.pw2 = QLineEdit()
        self.pw2.setEchoMode(QLineEdit.Password)
        f2.addRow("Your name", self.full)
        f2.addRow("Username", self.username)
        f2.addRow("Password", self.pw1)
        f2.addRow("Confirm password", self.pw2)
        rl.addLayout(f2)
        rl.addStretch(1)
        btn = button("Start using SDJ Mobile Care", "primary", None, self._save)
        btn.setMinimumHeight(40)
        rl.addWidget(btn)
        self.stack.addWidget(right)

    def restore_phone(self):
        from .offsite_ui import RestoreFromPhoneDialog
        dlg = RestoreFromPhoneDialog(self)
        dlg.exec()
        if dlg.restored:
            self.restored = True
            self.accept()

    def restore_file(self):
        from .offsite_ui import restore_from_file
        if restore_from_file(self):
            from .widgets import info
            info(self, "Restored", "The shop's data is back. Sign in with your usual username and password.")
            self.restored = True
            self.accept()

    def _save(self):
        if self.pw1.text() != self.pw2.text():
            error(self, "The two passwords do not match.")
            return
        try:
            auth.create_user(self.username.text(), self.pw1.text(), "ADMIN", self.full.text(), check_perm=False)
            settings.set_many(dict(shop_name=self.shop.text().strip() or "SDJ Mobile Care",
                                   shop_address=self.address.toPlainText().strip(), shop_phone=self.phone.text().strip(),
                                   shop_gstin=self.gstin.text().strip().upper(), gst_enabled=self.gst.isChecked()))
            self.user = auth.authenticate(self.username.text(), self.pw1.text())
        except BusinessError as exc:
            error(self, str(exc))
            return
        auth.set_current_user(self.user)
        self.accept()
