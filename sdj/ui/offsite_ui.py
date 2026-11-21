"""Off-PC backups on the PC: Settings → Backup card, recovery code, restore from phone / from a file, and the
emergency rebuild from a phone (first screen of a new PC)."""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel,
                               QLineEdit, QStackedWidget, QVBoxLayout, QWidget)

from .. import config
from ..core import auth, offsite, phonesync, settings, syncserver, winfirewall
from ..core.auth import current_user
from ..core.errors import BusinessError
from .bus import toast
from .widgets import Card, button, confirm, error, hbox, info, label, run_safely

log = logging.getLogger(__name__)


def when(v) -> str:
    if not v:
        return ""
    if isinstance(v, str):
        try:
            v = datetime.fromisoformat(v)
        except ValueError:
            return v
    return v.strftime("%d %b %Y, %I:%M %p")


def size_text(n: int) -> str:
    n = int(n or 0)
    return f"{n / 1024 / 1024:.1f} MB" if n >= 1024 * 1024 else f"{max(n // 1024, 1)} KB"


def describe(inf: dict) -> str:
    rows = []
    if inf.get("shop"):
        rows.append(f"Shop: {inf['shop']}")
    if inf.get("created"):
        rows.append(f"Backup made: {when(inf['created'])}" + (f"  (closing of {inf['date']})" if inf.get("date") else ""))
    c = inf.get("counts") or {}
    if c:
        rows.append(f"{c.get('products', 0)} products · {c.get('customers', 0)} customers · {c.get('sales', 0)} bills · "
                    f"{c.get('service_jobs', 0)} repair jobs")
    rows.append(f"File: {inf.get('name', '')} ({size_text(inf.get('size', 0))})")
    return "\n".join(rows)


# ================================================================ small dialogs
class SecretDialog(QDialog):
    """Owner password (as it was when the backup was made) or the recovery code."""

    def __init__(self, parent, inf: dict | None = None, ok: str = "Restore this backup"):
        super().__init__(parent)
        self.setWindowTitle("Unlock the backup")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        if inf:
            lay.addWidget(label(describe(inf), wrap=True))
        lay.addWidget(label("This backup is locked. Enter the owner's password (the one in use when the backup was made). "
                            "Forgot it? Type the 24-letter recovery code instead.", "Muted", wrap=True))
        self.secret = QLineEdit()
        self.secret.setEchoMode(QLineEdit.Password)
        self.secret.setPlaceholderText("Owner password or recovery code")
        show = QCheckBox("Show")
        show.toggled.connect(lambda on: self.secret.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        lay.addLayout(hbox(self.secret, show))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText(ok)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.secret.setFocus()


class RecoveryCodeDialog(QDialog):
    def __init__(self, parent, code: str):
        super().__init__(parent)
        self.code = code
        self.setWindowTitle("Backup recovery code")
        self.setMinimumWidth(620)
        lay = QVBoxLayout(self)
        lay.addWidget(label("Your recovery code", "PageTitle"))
        lay.addWidget(label("It opens every backup made from now on – even if the owner's password is forgotten. "
                            "It is shown only now: print it or write it down, and keep it away from the shop PC.",
                            wrap=True))
        self.code_lbl = QLabel(code)
        self.code_lbl.setAlignment(Qt.AlignCenter)
        self.code_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.code_lbl.setStyleSheet("font-size: 26px; font-weight: 800; letter-spacing: 2px; padding: 14px; "
                                    "font-family: Consolas, 'DejaVu Sans Mono', monospace; border: 2px dashed #CFAA51;")
        lay.addWidget(self.code_lbl)
        lay.addWidget(label("Anyone with this code and a backup file can open the shop data – do not share a photo of it.",
                            "Muted", wrap=True))
        self.done_box = QCheckBox("I have printed it or written it down")
        self.ok_btn = button("Done", "primary", None, self.accept)
        self.ok_btn.setEnabled(False)
        self.done_box.toggled.connect(self.ok_btn.setEnabled)
        lay.addLayout(hbox(button("Print / save as PDF", "", "printer", self.print_code),
                           button("Copy", "", "copy", lambda: QApplication.clipboard().setText(self.code)),
                           None, self.done_box, self.ok_btn))

    def print_code(self) -> None:
        from ..output.pdf_recovery import recovery_code_pdf
        from .printing import show_pdf
        path = config.documents_root() / f"Backup_Recovery_Code_{datetime.now():%Y-%m-%d}.pdf"
        try:
            recovery_code_pdf(self.code, settings.shop_info() | {"shop_logo_path": settings.get("shop_logo_path")},
                              path)
        except Exception as exc:
            log.exception("recovery code PDF failed")
            error(self, f"Could not make the PDF: {exc}")
            return
        show_pdf(path, self, "Recovery code")


def create_recovery_code(parent) -> bool:
    from .phone_link import PasswordDialog
    made = offsite.recovery_code_created()
    if made and not confirm(parent, "Make a new recovery code?",
                            f"You already have a recovery code (made {when(made)}). A new code is used for backups made "
                            "from now on; older backups still open with the old code. Continue?", "Make new code"):
        return False
    dlg = PasswordDialog(parent, "Recovery code", "Enter your password to create the backup recovery code.",
                         ok="Create code")
    if dlg.exec() != QDialog.Accepted:
        return False
    code = run_safely(parent, offsite.create_recovery_code, dlg.pw.text())
    if not code:
        return False
    try:                                 # a fresh backup that already opens with the new code
        offsite.create_bundle("manual")
    except Exception:
        log.exception("backup after new recovery code failed")
    RecoveryCodeDialog(parent, code).exec()
    return True


def do_restore(parent, path: str, secret: str) -> dict | None:
    QApplication.setOverrideCursor(Qt.WaitCursor)
    problem = None
    result = None
    try:
        result = offsite.restore_any(path, secret)
    except BusinessError as exc:
        problem = str(exc)
    except Exception as exc:  # logged; the current data was kept
        log.exception("restore failed")
        problem = f"The backup could not be restored: {exc}\n\nThe current data was not changed."
    finally:
        QApplication.restoreOverrideCursor()
    if problem:
        error(parent, problem)
        return None
    return result


def restore_from_file(parent, start_dir: str = "") -> bool:
    """Pen drive / Google Drive download / Downloads folder: .sdjb (locked) or .db (plain local backup)."""
    path, _ = QFileDialog.getOpenFileName(parent, "Choose a backup file", start_dir or str(Path.home()),
                                          "SDJ backups (*.sdjb *.db);;All files (*)")
    if not path:
        return False
    try:
        inf = offsite.describe_file(path)
    except BusinessError as exc:
        error(parent, str(exc))
        return False
    if auth.user_count() > 0 and not confirm(
            parent, "Replace all data?", "ALL current data on this PC will be replaced with this backup. A safety copy "
                                         f"of the current data is saved first.\n\n{describe(inf)}", "Continue",
            danger=True):
        return False
    secret = ""
    if inf["kind"] == "bundle":
        dlg = SecretDialog(parent, inf)
        if dlg.exec() != QDialog.Accepted:
            return False
        secret = dlg.secret.text()
    elif auth.user_count() == 0 and not confirm(parent, "Restore this backup?", describe(inf), "Restore"):
        return False
    return do_restore(parent, path, secret) is not None


# ================================================================ restore from phone (+ emergency rebuild)
class _Relay(QObject):
    got = Signal(str, object)


class RestoreFromPhoneDialog(QDialog):
    """Shows a restore code / QR; the phone app sends a backup (or its shop copy) to this PC over the Wi-Fi."""

    def __init__(self, parent=None, in_app: bool = False):
        super().__init__(parent)
        from .phone_link import QrWidget
        self.in_app = in_app
        self.restored = False
        self.file_path = ""
        self.copy_path = ""
        self._expires = None
        self.setWindowTitle("Restore from phone")
        self.resize(900, 640)
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.addWidget(label("Restore from phone", "PageTitle"))
        root.addWidget(label("The SDJ phone app keeps a backup after every daily closing. Send it to this PC over the "
                             "shop Wi-Fi – no internet is needed.", "Muted", wrap=True))
        top = QHBoxLayout()
        top.setSpacing(22)
        self.qr = QrWidget()
        top.addWidget(self.qr)
        right = QVBoxLayout()
        right.addWidget(label("Restore code", "SectionHint"))
        self.code_lbl = QLabel()
        self.code_lbl.setStyleSheet("font-size: 32px; font-weight: 800; letter-spacing: 3px; "
                                    "font-family: Consolas, 'DejaVu Sans Mono', monospace;")
        self.code_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        right.addWidget(self.code_lbl)
        self.expiry_lbl = label("", "Muted")
        right.addWidget(self.expiry_lbl)
        right.addSpacing(6)
        right.addWidget(label("On the phone (SDJ Mobile Care app):\n"
                              "1. Connect the phone to the same Wi-Fi as this PC.\n"
                              "2. Open the menu → Backups.\n"
                              "3. Choose a backup → 'Send to PC' → scan this QR.\n\n"
                              "No backup on the phone? In Backups tap 'Rebuild a new PC from this phone' – it brings "
                              "products, stock, IMEIs, customers with dues and open repair jobs.", wrap=True))
        self.addr_lbl = label("", "Muted", wrap=True)
        self.addr_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        right.addWidget(self.addr_lbl)
        self.fw_lbl = label("", wrap=True)
        self.fw_btn = button("Allow in Windows Firewall", "", "shield-check", self.fix_firewall)
        self.fw_row = QWidget()
        fl = QHBoxLayout(self.fw_row)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.addWidget(self.fw_lbl, 1)
        fl.addWidget(self.fw_btn)
        self.fw_row.setVisible(False)
        right.addWidget(self.fw_row)
        right.addStretch(1)
        top.addLayout(right, 1)
        root.addLayout(top)

        self.pages = QStackedWidget()
        wait = Card("Waiting for the phone…")
        self.wait_lbl = label("Keep this window open. The backup appears here as soon as the phone sends it.", "Muted",
                              wrap=True)
        wait.add(self.wait_lbl)
        self.pages.addWidget(wait)
        filec = Card("Backup received from the phone")
        self.file_lbl = label("", wrap=True)
        filec.add(self.file_lbl)
        self.secret = QLineEdit()
        self.secret.setEchoMode(QLineEdit.Password)
        self.secret.setPlaceholderText("Owner password (as when the backup was made) or recovery code")
        self.secret.returnPressed.connect(self.restore_file)
        show = QCheckBox("Show")
        show.toggled.connect(lambda on: self.secret.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        filec.add(hbox(self.secret, show, button("Restore this backup", "primary", "archive-restore", self.restore_file)))
        self.pages.addWidget(filec)
        copyc = Card("Shop copy received from the phone (emergency rebuild)")
        self.copy_lbl = label("", wrap=True)
        copyc.add(self.copy_lbl)
        self.owner_pw = QLineEdit()
        self.owner_pw.setEchoMode(QLineEdit.Password)
        self.owner_pw.setPlaceholderText("Owner password (same as on the old PC)")
        self.owner_pw.returnPressed.connect(self.rebuild)
        copyc.add(hbox(self.owner_pw, button("Rebuild this PC from the phone", "primary", "smartphone", self.rebuild)))
        copyc.add(label("Not on the phone, so not brought back: old bills, purchases, expenses, cost prices (fill them in "
                        "Products later) and the shop logo. Use a backup instead whenever you have one.", "Muted",
                        wrap=True))
        self.pages.addWidget(copyc)
        root.addWidget(self.pages)
        root.addLayout(hbox(button("New code", "", "refresh-cw", self.new_code), None,
                            button("Close", "", None, self.reject)))

        self._relay = _Relay()
        self._relay.got.connect(self._on_event, Qt.QueuedConnection)
        self._sub = lambda e, i: self._relay.got.emit(e, dict(i))
        phonesync.subscribe(self._sub)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.new_code()

    # ---- code / service
    def new_code(self) -> None:
        inf = run_safely(self, phonesync.new_restore_code)
        if not inf:
            return
        svc = syncserver.ensure_running()
        self.code_lbl.setText(inf["display"])
        self.qr.set_data(inf["qr"])
        self._expires = inf["expires"]
        ips = inf["ips"]
        if not svc.running:
            self.addr_lbl.setText("⚠ " + (svc.error or "The network service could not start on this PC."))
        elif ips:
            self.addr_lbl.setText("This PC: " + ", ".join(ips) + f"  (port {phonesync.PORT}). Test from the phone's "
                                  f"browser: http://{ips[0]}:{phonesync.PORT}")
        else:
            self.addr_lbl.setText("⚠ This PC is not connected to a network. Connect it to the shop Wi-Fi / router.")
        if winfirewall.supported():
            ok = winfirewall.rule_present()
            self.fw_row.setVisible(not ok)
            self.fw_lbl.setText("⚠ Windows Firewall may block the phone. Click to allow (Windows asks once).")
        self.timer.start(1000)
        self._tick()

    def fix_firewall(self) -> None:
        ok, msg = winfirewall.allow()
        self.fw_row.setVisible(not winfirewall.rule_present())
        (toast if ok else (lambda m: error(self, m)))(msg)

    def _tick(self) -> None:
        left = int((self._expires - datetime.now()).total_seconds()) if self._expires else 0
        if left <= 0:
            self.timer.stop()
            self.expiry_lbl.setText("The code has expired – click 'New code'.")
            self.qr.set_data("")
            return
        self.expiry_lbl.setText(f"Valid for {left // 60}:{left % 60:02d} more")

    # ---- what the phone sent
    def _on_event(self, event: str, inf: dict) -> None:
        if event == "restore_file":
            self.file_path = inf.get("path", "")
            phone = f"From: {inf['phone']}\n" if inf.get("phone") else ""
            self.file_lbl.setText(phone + describe(inf.get("info") or {}))
            locked = (inf.get("info") or {}).get("kind") == "bundle"
            self.secret.setVisible(locked)
            self.pages.setCurrentIndex(1)
            self.raise_()
            self.activateWindow()
            (self.secret if locked else self.file_lbl).setFocus()
        elif event == "rebuild":
            self.copy_path = inf.get("path", "")
            s = inf.get("summary") or {}
            self.copy_lbl.setText(
                (f"From: {s.get('phone')}\n" if s.get("phone") else "")
                + (f"Shop: {s.get('shop')}\n" if s.get("shop") else "")
                + f"{s.get('products', 0)} products ({s.get('stock', 0)} pieces in stock) · {s.get('imeis', 0)} IMEIs · "
                  f"{s.get('customers', 0)} customers (dues ₹{s.get('dues', 0)}) · {s.get('open_jobs', 0)} open repair "
                  f"jobs\nLogins: {', '.join(s.get('users') or [])}")
            self.pages.setCurrentIndex(2)
            self.raise_()
            self.activateWindow()
            self.owner_pw.setFocus()

    def restore_file(self) -> None:
        if not self.file_path:
            return
        if self.in_app and not confirm(self, "Replace all data?",
                                       "ALL current data on this PC will be replaced with this backup. A safety copy of "
                                       "the current data is saved first.", "Restore", danger=True):
            return
        if do_restore(self, self.file_path, self.secret.text()) is None:
            self.secret.selectAll()
            return
        self.restored = True
        info(self, "Restored", "The backup was restored."
             + (" SDJ Mobile Care will now close – please open it again." if self.in_app else
                " Sign in with your usual username and password."))
        self.accept()

    def rebuild(self) -> None:
        if not self.copy_path:
            return
        try:
            req = json.loads(Path(self.copy_path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            error(self, f"The phone's copy could not be read: {exc}")
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            summary = offsite.rebuild_from_phone(req, self.owner_pw.text())
        except BusinessError as exc:
            QApplication.restoreOverrideCursor()
            error(self, str(exc))
            return
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            log.exception("rebuild failed")
            error(self, f"The PC could not be rebuilt: {exc}")
            return
        QApplication.restoreOverrideCursor()
        Path(self.copy_path).unlink(missing_ok=True)
        self.restored = True
        notes = summary.get("notes") or []
        info(self, "PC rebuilt from the phone",
             f"Done: {summary['products']} products, {summary['customers']} customers and {summary['open_jobs']} open "
             "repair jobs are back. The phone stays linked.\n\nNot brought back: old bills, purchases, expenses, cost "
             "prices (fill them in Products) and the shop logo.\n\nSign in with the same username and password as "
             "before." + ("\n\nPlease check:\n• " + "\n• ".join(notes[:12]) if notes else ""))
        self.accept()

    def done(self, r: int) -> None:  # noqa: D401 - closing in any way cancels the code
        self.timer.stop()
        phonesync.unsubscribe(self._sub)
        phonesync.cancel_restore_code()
        try:
            syncserver.ensure_running()
        except Exception:
            log.exception("sync service check failed")
        super().done(r)


# ================================================================ Settings → Backup card
class OffsiteCard(Card):
    def __init__(self, parent=None):
        super().__init__("Off-PC backups", "locked with the owner's password · made after every daily closing and by "
                                           "itself when data changes · kept on the phone and a 2nd folder")
        self.owner = current_user().can("settings")
        self.status_lbl = label("", wrap=True)
        self.status_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.add(self.status_lbl)
        self.warn_lbl = label("", "BadText", wrap=True)
        self.add(self.warn_lbl)
        self.b_now = button("Make off-PC backup now", "primary", "hard-drive-upload", self.backup_now)
        self.b_folder = button("Choose 2nd backup folder…", "", "folder", self.choose_folder)
        self.b_unset = button("Remove 2nd folder", "ghost", None, self.remove_folder)
        self.b_open = button("Open 2nd folder", "ghost", None, self.open_folder)
        self.add(hbox(self.b_now, self.b_folder, self.b_unset, self.b_open, None))
        self.b_auto = QCheckBox("Also make one by itself: when the program opens, every 2 hours and when it closes "
                                "(only when something changed). Phones and the 2nd folder keep the newest of each day.")
        self.b_auto.setChecked(bool(settings.get("auto_offsite")))
        self.b_auto.toggled.connect(lambda on: settings.set_value("auto_offsite", bool(on)))
        self.add(self.b_auto)
        self.b_code = button("Create recovery code…", "", "key-round", self.make_code)
        self.b_phone = button("Restore from phone…", "danger", "smartphone", self.restore_phone)
        self.add(hbox(self.b_code, self.b_phone, None))
        self.add(label("Google Drive: install 'Google Drive for desktop' on this PC and choose a folder inside its drive "
                       "(for example G:\\My Drive\\SDJ Backups) as the 2nd backup folder – every backup then goes to "
                       "your Google Drive by itself. A pen drive works the same way.", "Muted", wrap=True))
        for b in (self.b_folder, self.b_unset, self.b_code, self.b_phone, self.b_auto):
            b.setVisible(self.owner)
        self.refresh()

    def refresh(self) -> None:
        try:
            st = offsite.status()
        except Exception:
            log.exception("off-PC status failed")
            return
        rows = []
        lt = st["latest"]
        rows.append(f"Last off-PC backup: {when(lt['created'])} ({size_text(lt['size'])}) – checked ✓" if lt else
                    "No off-PC backup yet – it is made when you close the day (or click 'Make off-PC backup now').")
        if st["phones"]:
            for p in st["phones"]:
                if p["keeps"]:
                    rows.append(f"Phone '{p['name']}': " + (f"has the backup of {when(p['last'])}" if p["last"] else
                                                            "no backup received yet (opens the app on the shop Wi-Fi)"))
            if not any(p["keeps"] for p in st["phones"]):
                rows.append("No linked phone keeps backups – turn it on in Settings → Phone link.")
        else:
            rows.append("Phone: no phone linked yet (Settings → Phone link) – the owner's phone keeps every backup.")
        if st["second_folder"]:
            rows.append(f"2nd folder: {st['second_folder']} – " + (f"copied {when(st['second_last'])}" if st["second_last"]
                                                                   else "not copied yet")
                        + (f"  ⚠ {st['second_error']}" if st["second_error"] else ""))
        else:
            rows.append("2nd folder: not chosen (pen drive or Google Drive for desktop folder).")
        rows.append(f"Recovery code: made {when(st['recovery'])}" if st["recovery"] else
                    "Recovery code: not made yet – make one and keep it on paper.")
        self.status_lbl.setText("\n".join(rows))
        self.warn_lbl.setText("\n".join("⚠ " + w for w in st["warnings"]))
        self.warn_lbl.setVisible(bool(st["warnings"]))
        self.b_unset.setVisible(self.owner and bool(st["second_folder"]))
        self.b_open.setVisible(bool(st["second_folder"]))
        self.b_code.setText("Make a new recovery code…" if st["recovery"] else "Create recovery code…")

    def backup_now(self) -> None:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            r = run_safely(self, offsite.create_bundle, "manual")
        finally:
            QApplication.restoreOverrideCursor()
        if r:
            toast(f"Off-PC backup made: {r['name']} – phones get it at the next sync")
        self.refresh()

    def choose_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "2nd backup folder (pen drive / Google Drive folder)",
                                             offsite.second_folder() or str(Path.home()))
        if d and run_safely(self, offsite.set_second_folder, d, success="2nd backup folder saved"):
            self.refresh()

    def remove_folder(self) -> None:
        if confirm(self, "Remove 2nd folder?", "Backups will no longer be copied there. Files already there stay.",
                   "Remove"):
            run_safely(self, offsite.set_second_folder, "")
            self.refresh()

    def open_folder(self) -> None:
        from .printing import open_folder
        if offsite.second_folder():
            open_folder(offsite.second_folder())

    def make_code(self) -> None:
        create_recovery_code(self)
        self.refresh()

    def restore_phone(self) -> None:
        dlg = RestoreFromPhoneDialog(self, in_app=True)
        dlg.exec()
        if dlg.restored:
            QApplication.instance().quit()


# ================================================================ now and then: pen drive back → copy the latest
_catch_up_busy = threading.Lock()


def catch_up_in_background() -> None:
    if not _catch_up_busy.acquire(blocking=False):
        return

    def run():
        try:
            offsite.catch_up_second_folder()
        finally:
            _catch_up_busy.release()
    threading.Thread(target=run, name="sdj-2nd-folder", daemon=True).start()


def backup_line() -> tuple[str, bool]:
    """Short status for the dashboard: where the last backup is, and whether something is late."""
    st = offsite.status()
    if st["latest"] is None:
        return "Off-PC backup: none yet – close the day to make the first one", False
    parts = []
    keep = [p for p in st["phones"] if p["keeps"]]
    if keep:
        parts.append("phone " + (when(st["phone_last"]) if st["phone_last"] else "not yet"))
    if st["second_folder"]:
        parts.append("2nd folder " + (when(st["second_last"]) if st["second_last"] else "not yet"))
    text = "Last backup: " + (" · ".join(parts) if parts else f"on this PC only ({when(st['latest']['created'])})")
    return text, st["late"]


__all__ = ["OffsiteCard", "RestoreFromPhoneDialog", "SecretDialog", "RecoveryCodeDialog", "restore_from_file",
           "create_recovery_code", "catch_up_in_background", "backup_line", "do_restore", "describe"]
