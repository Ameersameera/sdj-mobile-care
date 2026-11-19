"""Settings → Phone link (one-time code + QR, linked phones, sync alerts) and the bridge to the sync service."""
from __future__ import annotations

import logging
import sys
from datetime import datetime

from PySide6.QtCore import QObject, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit,
                               QStackedWidget, QTextEdit, QVBoxLayout, QWidget)

from ..core import auth, phonesync, syncserver, winfirewall
from ..output.fmt import dt
from .bus import bus, changed, toast
from .widgets import Card, Col, DataTable, button, confirm, error, hbox, label, run_safely, scroll_wrap

log = logging.getLogger(__name__)


# ================================================================ bridge: network threads → screens
class SyncBridge(QObject):
    """Phone entries arrive on a background thread; this relays them to the UI thread."""

    event = Signal(str, object)
    devices_changed = Signal()

    def __init__(self):
        super().__init__()
        self.event.connect(self._on_event, Qt.QueuedConnection)
        phonesync.subscribe(self._relay)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check_service)
        self.timer.start(20_000)
        self.check_service()

    def _relay(self, event: str, info: dict) -> None:      # any thread
        self.event.emit(event, dict(info))

    def check_service(self) -> None:
        try:
            syncserver.ensure_running()
        except Exception:
            log.exception("phone sync service check failed")

    def _on_event(self, event: str, info: dict) -> None:   # UI thread
        if event == "synced":
            changed("all")
            n, bad = info.get("saved", 0), info.get("problems", 0)
            msg = f"Phone '{info.get('name')}' synced · {n} entr{'y' if n == 1 else 'ies'} saved"
            if bad:
                msg += f" · {bad} need checking (Settings → Phone link → Sync alerts)"
            toast(msg, "error" if bad else "info")
        elif event == "linked":
            toast(f"Phone '{info.get('name')}' is linked to this PC")
            self.check_service()
            self.devices_changed.emit()
        elif event in ("removed", "code"):
            self.check_service()
            self.devices_changed.emit()
        elif event == "alerts":
            changed("all")
        elif event == "seen":
            self.devices_changed.emit()


_bridge: SyncBridge | None = None


def start_bridge() -> SyncBridge:
    global _bridge
    if _bridge is None:
        _bridge = SyncBridge()
    return _bridge


def bridge() -> SyncBridge | None:
    return _bridge


# ================================================================ Windows start-up
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "SDJMobileCare"


def autostart_supported() -> bool:
    return sys.platform.startswith("win") and bool(getattr(sys, "frozen", False))


def autostart_enabled() -> bool:
    if not autostart_supported():
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
            return True
    except OSError:
        return False


def set_autostart(on: bool) -> None:
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, f'"{sys.executable}"')
        else:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except OSError:
                pass


# ================================================================ widgets
class QrWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.matrix: list[list[bool]] = []
        self.setMinimumSize(230, 230)

    def sizeHint(self):  # noqa: N802
        return QSize(240, 240)

    def set_data(self, text: str) -> None:
        self.matrix = phonesync.qr_matrix(text) if text else []
        self.update()

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        side = min(self.width(), self.height())
        x0, y0 = (self.width() - side) / 2, (self.height() - side) / 2
        p.fillRect(QRectF(x0, y0, side, side), QColor("white"))
        if self.matrix:
            n = len(self.matrix) + 8                     # 4-module quiet zone on each side
            cell = side / n
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#111111"))
            for r, row in enumerate(self.matrix):
                for c, dark in enumerate(row):
                    if dark:
                        p.drawRect(QRectF(x0 + (c + 4) * cell, y0 + (r + 4) * cell, cell + 0.4, cell + 0.4))
        p.end()


class PasswordDialog(QDialog):
    def __init__(self, parent, title: str, text: str, ok: str = "Continue", danger: bool = False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(440)
        lay = QVBoxLayout(self)
        lay.addWidget(label(text, wrap=True))
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.Password)
        self.pw.setPlaceholderText("Your password")
        lay.addWidget(self.pw)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText(ok)
        if danger:
            bb.button(QDialogButtonBox.Ok).setObjectName("DangerBtn")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.pw.setFocus()


class SyncAlertsDialog(QDialog):
    """Things to check after a phone synced: last piece sold twice, closed day, bill changed on both …"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Phone sync alerts")
        self.resize(1080, 640)
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(label("Entries from phones are always saved first; these need your decision or a quick check.",
                            "Muted", wrap=True), 1)
        self.show_all = QCheckBox("Show checked alerts too")
        self.show_all.toggled.connect(self.refresh)
        top.addWidget(self.show_all)
        lay.addLayout(top)
        self.table = DataTable([Col("created_at", "When", "datetime", 150), Col("phone", "Phone", width=140),
                                Col("kind_label", "Type", width=200, bold=True), Col("title", "What happened"),
                                Col("status", "Status", "status", 100)], search=False, stretch="title")
        self.table.selection_changed.connect(self._selected)
        lay.addWidget(self.table, 3)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(130)
        lay.addWidget(self.details, 1)
        self.b_bill = button("Open bill", "", "receipt", self.open_bill)
        self.b_phone = button("Use phone version", "primary", "smartphone", self.use_phone)
        self.b_pc = button("Keep PC version", "", "store", lambda: self._resolve("PC version kept"))
        self.b_close = button("Update closing", "primary", "calendar-check", self.update_closing)
        self.b_ok = button("Mark as checked", "", "check", lambda: self._resolve("Checked"))
        lay.addLayout(hbox(self.b_bill, self.b_phone, self.b_pc, self.b_close, None, self.b_ok,
                           button("Close", "", None, self.accept)))
        self.refresh()

    def refresh(self) -> None:
        rows = phonesync.list_alerts(None if self.show_all.isChecked() else "OPEN")
        self.table.set_rows(rows)
        self._selected()

    def _row(self) -> dict | None:
        return self.table.selected()

    def _selected(self) -> None:
        r = self._row()
        self.details.setPlainText((r["title"] + "\n\n" + r["details"]
                                   + (f"\n\nResolved: {r['resolution']} – {r['resolved_by']}" if r["status"] != "OPEN"
                                      else "")) if r else "")
        is_open = bool(r and r["status"] == "OPEN")
        self.b_bill.setEnabled(bool(r and r["ref_type"] == "SALE" and r["ref_id"]))
        self.b_phone.setVisible(bool(r and r["kind"] == "BILL_CONFLICT"))
        self.b_pc.setVisible(bool(r and r["kind"] == "BILL_CONFLICT"))
        self.b_phone.setEnabled(bool(r and r["can_use_phone"]))
        self.b_pc.setEnabled(is_open)
        self.b_close.setVisible(bool(r and r["kind"] == "CLOSED_DAY"))
        self.b_close.setEnabled(bool(r and r["can_update_closing"]))
        self.b_ok.setEnabled(is_open)

    def open_bill(self) -> None:
        r = self._row()
        if r and r["ref_id"]:
            from .dialogs import SaleDetailDialog
            SaleDetailDialog(self, r["ref_id"]).exec()

    def use_phone(self) -> None:
        r = self._row()
        if r and confirm(self, "Use the phone's version?",
                         "The bill will be changed to what was entered on the phone (the PC's version is kept in "
                         "Sales History as an older version)."):
            if run_safely(self, phonesync.use_phone_version, r["id"], success="Phone version applied", topic="all"):
                self.refresh()

    def update_closing(self) -> None:
        r = self._row()
        if r and run_safely(self, phonesync.update_closing_from_alert, r["id"], success="Closing updated",
                            topic="closing"):
            self.refresh()

    def _resolve(self, how: str) -> None:
        r = self._row()
        if r and run_safely(self, phonesync.resolve_alert, r["id"], how, topic="all"):
            self.refresh()


# ================================================================ the Settings tab
class PhoneLinkTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        root.addWidget(self.stack)
        self.stack.addWidget(self._locked_page())
        self.content = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        b = bridge()
        if b is not None:
            b.devices_changed.connect(self._refresh_if_open)

    # ---- locked
    def _locked_page(self) -> QWidget:
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 12, 8, 12)
        c = Card("Phone link", "use the SDJ phone app with the same data · over the shop Wi-Fi · no internet or cloud")
        c.add(label("Phones linked here can make bills, take repair jobs, record expenses and collect dues – even "
                    "offline. Entries made on either side appear on both when the phone is on the same Wi-Fi as "
                    "this PC.", wrap=True))
        c.add(label("Enter your password to open the phone link settings.", "Muted"))
        self.unlock_pw = QLineEdit()
        self.unlock_pw.setEchoMode(QLineEdit.Password)
        self.unlock_pw.setPlaceholderText("Your password")
        self.unlock_pw.setFixedWidth(260)
        self.unlock_pw.returnPressed.connect(self.unlock)
        c.add(hbox(self.unlock_pw, button("Open", "primary", "lock", self.unlock), None))
        lay.addWidget(c)
        lay.addStretch(1)
        return host

    def unlock(self) -> None:
        if run_safely(self, auth.check_own_password, self.unlock_pw.text()):
            self.unlock_pw.clear()
            if self.content is None:
                self.content = self._content_page()
                self.stack.addWidget(self.content)
            self.stack.setCurrentWidget(self.content)
            self.refresh()

    # ---- content
    def _content_page(self) -> QWidget:
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 12, 8, 12)
        lay.setSpacing(12)

        st = Card("Phone sync on this PC")
        self.status_lbl = label("", wrap=True)
        self.addr_lbl = label("", "Muted", wrap=True)
        st.add(self.status_lbl)
        st.add(self.addr_lbl)
        self.fw_lbl = label("", wrap=True)
        self.fw_btn = button("Allow phone sync in Windows Firewall", "primary", "shield-check", self.fix_firewall)
        self.fw_row = QWidget()
        self.fw_row.setLayout(hbox(self.fw_lbl, None, self.fw_btn))
        self.fw_row.setVisible(winfirewall.supported())
        st.add(self.fw_row)
        self.test_lbl = label("", "Muted", wrap=True)
        self.test_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        st.add(self.test_lbl)
        self._fw_ok = None
        self.autostart = QCheckBox("Start SDJ Mobile Care when Windows starts (so phones can sync after a power cut "
                                   "without anyone opening the app)")
        self.autostart.setVisible(autostart_supported())
        self.autostart.setChecked(autostart_enabled())
        self.autostart.toggled.connect(self._toggle_autostart)
        st.add(self.autostart)
        lay.addWidget(st)

        lk = Card("Link a new phone", "one-time code · valid 10 minutes")
        self.user_combo = QComboBox()
        self.user_combo.setMinimumWidth(260)
        lk.add(hbox(QLabel("Phone signs in as"), self.user_combo,
                    button("Create one-time code", "primary", "qr-code", self.create_code), None))
        lk.add(label("The phone gets the same permissions as this account. Staff phones: choose the staff account.",
                     "Muted", wrap=True))
        self.code_box = QWidget()
        cb = QHBoxLayout(self.code_box)
        cb.setContentsMargins(0, 6, 0, 0)
        cb.setSpacing(22)
        self.qr = QrWidget()
        cb.addWidget(self.qr)
        right = QVBoxLayout()
        self.code_lbl = QLabel()
        self.code_lbl.setStyleSheet("font-size: 34px; font-weight: 800; letter-spacing: 3px; "
                                    "font-family: Consolas, 'DejaVu Sans Mono', monospace;")
        self.code_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        right.addWidget(label("One-time code", "SectionHint"))
        right.addWidget(self.code_lbl)
        self.code_for = label("", "Muted")
        self.expiry_lbl = label("", "Muted")
        right.addWidget(self.code_for)
        right.addWidget(self.expiry_lbl)
        right.addSpacing(8)
        right.addWidget(label("On the phone:\n1. Install and open the SDJ Mobile Care phone app.\n"
                              "2. Connect the phone to this shop's Wi-Fi.\n3. Tap 'Scan QR' (or type the code).\n"
                              "4. Sign in with the account's password.", wrap=True))
        right.addWidget(button("Cancel code", "", "x", self.cancel_code), 0, Qt.AlignLeft)
        right.addStretch(1)
        cb.addLayout(right, 1)
        self.code_box.setVisible(False)
        lk.add(self.code_box)
        lay.addWidget(lk)

        ph = Card("Linked phones")
        self.dev_table = DataTable([Col("name", "Phone", width=180, bold=True), Col("user_name", "Signs in as", width=150),
                                    Col("series", "Bill series", width=90), Col("linked_at", "Linked on", "datetime", 150),
                                    Col("last_seen_at", "Last sync", "datetime", 150), Col("last_ip", "Address", width=120),
                                    Col("alerts", "Open alerts", "int", 110)], search=False, stretch="name")
        self.dev_table.setMinimumHeight(170)
        ph.add(self.dev_table)
        ph.add(hbox(button("Remove phone", "danger", "trash-2", self.remove_phone),
                    label("for a lost or replaced phone – it stops syncing at once; its old entries stay", "Muted"),
                    None, button("Refresh", "", "refresh-cw", self.refresh)))
        lay.addWidget(ph)

        al = Card("Sync alerts", "last piece sold on both, bill changed on both, entries for a closed day …")
        self.alert_lbl = label("")
        al.add(hbox(self.alert_lbl, None, button("Open sync alerts", "primary", "circle-alert", self.open_alerts)))
        lay.addWidget(al)
        lay.addStretch(1)
        return scroll_wrap(host)

    def _refresh_if_open(self) -> None:
        if self.content is not None and self.stack.currentWidget() is self.content:
            self.refresh(light=True)

    def check_firewall(self) -> None:
        self._fw_ok = winfirewall.rule_present()
        self._show_firewall()

    def _show_firewall(self) -> None:
        if not winfirewall.supported():
            return
        if self._fw_ok:
            self.fw_lbl.setText("✓ Windows Firewall allows phone sync.")
            self.fw_btn.setText("Allow again")
        else:
            self.fw_lbl.setText("⚠ Windows Firewall may be blocking phones (common reason for 'Could not reach the PC'). "
                                "Click the button – Windows will ask for permission.")
            self.fw_btn.setText("Allow phone sync in Windows Firewall")

    def fix_firewall(self) -> None:
        ok, msg = winfirewall.allow()
        self.check_firewall()
        (toast if ok else (lambda m: error(self, m)))(msg)

    def refresh(self, light: bool = False) -> None:
        if self.content is None:
            return
        if not light and winfirewall.supported():
            self.check_firewall()
        svc = syncserver.service()
        ips = phonesync.local_ips()
        if svc.running:
            self.status_lbl.setText("● Phone sync is running. " + phonesync.last_seen_summary())
        elif svc.error:
            self.status_lbl.setText("⚠ " + svc.error)
        else:
            self.status_lbl.setText("Phone sync starts when you link the first phone.")
        self.addr_lbl.setText(("This PC's address on the network: " + ", ".join(ips) + f"  (port {phonesync.PORT}) – "
                               "type it in the phone only if it cannot find the PC by itself.") if ips else
                              "This PC is not connected to a network right now.")
        self.test_lbl.setText((f"Check from the phone: open  http://{ips[0]}:{phonesync.PORT}  in Chrome – it should say "
                               "'SDJ Mobile Care phone sync is working'. If it does not open, the firewall, an antivirus "
                               "or the Wi-Fi router is blocking it.") if ips and svc.running else "")
        self.dev_table.set_rows(phonesync.list_devices())
        n = phonesync.open_alert_count()
        self.alert_lbl.setText(f"{n} open alert{'s' if n != 1 else ''}" if n else "No open alerts – all good.")
        self._show_code(phonesync.active_code())
        if light:
            return
        cur = self.user_combo.currentData()
        self.user_combo.clear()
        me = auth.current_user()
        for u in auth.list_users():
            if u["is_active"]:
                self.user_combo.addItem(f"{u['full_name'] or u['username']} ({u['role_label']})", u["id"])
        idx = self.user_combo.findData(cur if cur else me.id)
        self.user_combo.setCurrentIndex(max(idx, 0))

    def _show_code(self, info: dict | None) -> None:
        self.code_box.setVisible(info is not None)
        if info is None:
            self.timer.stop()
            return
        if self.code_lbl.text() != info["display"]:
            self.code_lbl.setText(info["display"])
            self.qr.set_data(info["qr"])
        self.code_for.setText(f"Phone will sign in as: {info['username']}")
        self._expires = info["expires"]
        self._tick()
        self.timer.start(1000)

    def _tick(self) -> None:
        left = int((self._expires - datetime.now()).total_seconds()) if getattr(self, "_expires", None) else 0
        if left <= 0:
            self.timer.stop()
            self.code_box.setVisible(False)
            return
        self.expiry_lbl.setText(f"Valid for {left // 60}:{left % 60:02d} more · works only once")

    def create_code(self) -> None:
        uid = self.user_combo.currentData()
        info = run_safely(self, phonesync.new_link_code, uid)
        if info:
            syncserver.ensure_running()
            svc = syncserver.service()
            if not svc.running:
                error(self, svc.error or "Phone sync could not start on this PC.")
            elif winfirewall.supported() and winfirewall.rule_present() is False and confirm(
                    self, "Allow phones through Windows Firewall?",
                    "Windows Firewall has no rule for phone sync yet, so the phone may not reach this PC. "
                    "Allow it now? Windows will ask for permission once."):
                self.fix_firewall()
            self.refresh()

    def cancel_code(self) -> None:
        phonesync.cancel_link_code()
        self.refresh()

    def remove_phone(self) -> None:
        row = self.dev_table.selected()
        if not row:
            error(self, "Select the phone to remove.")
            return
        dlg = PasswordDialog(self, "Remove phone",
                             f"Remove '{row['name']}' (signs in as {row['user_name']})? It will stop syncing at once. "
                             "Entries it already sent stay in the shop data. Enter your password to confirm.",
                             ok="Remove phone", danger=True)
        if dlg.exec() == QDialog.Accepted and run_safely(self, phonesync.remove_device, row["id"], dlg.pw.text(),
                                                         success="Phone removed"):
            self.refresh()

    def open_alerts(self) -> None:
        SyncAlertsDialog(self).exec()
        self.refresh()

    def _toggle_autostart(self, on: bool) -> None:
        try:
            set_autostart(on)
            toast("SDJ Mobile Care will start with Windows" if on else "Start with Windows turned off")
        except OSError as exc:
            error(self, f"Could not change the Windows start-up setting: {exc}")


__all__ = ["SyncBridge", "start_bridge", "bridge", "PhoneLinkTab", "SyncAlertsDialog", "QrWidget", "dt", "bus"]
