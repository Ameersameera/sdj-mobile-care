"""In-app updates: the 'new version' popup, Settings → About → Check for updates, and the silent self-update."""
from __future__ import annotations

import logging
import threading
import webbrowser

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QTextBrowser,
                               QVBoxLayout)

from .. import APP_VERSION
from ..core import settings, updater
from ..core.auth import current_user
from .bus import toast
from .widgets import button, error, info, label

log = logging.getLogger(__name__)


def _is_owner() -> bool:
    u = current_user()
    return bool(u is not None and getattr(u, "role", "") == "ADMIN")


class _Bridge(QObject):
    checked = Signal(dict, bool)         # result, manual
    progress = Signal(int, int)
    failed = Signal(str)
    ready = Signal(str)


class UpdateDialog(QDialog):
    """'Version X is ready' – Update now / Later / Skip this version. Download and install happen in here."""

    def __init__(self, latest: dict, parent=None):
        super().__init__(parent)
        self.latest = latest
        self.setWindowTitle("Update available")
        self.setMinimumWidth(520)
        self._busy = False
        self._cancel = False
        self.installer = None
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.addWidget(label(f"SDJ Mobile Care {latest['version']} is ready", "SectionTitle"))
        lay.addWidget(label(f"You have version {APP_VERSION}.", "Muted"))
        notes = QTextBrowser()
        notes.setOpenLinks(False)
        notes.setPlainText(self._notes(latest.get("notes") or ""))
        notes.setMinimumHeight(170)
        lay.addWidget(notes, 1)
        self.status = label("", "Muted", wrap=True)
        lay.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.setVisible(False)
        lay.addWidget(self.bar)
        can = updater.can_self_install()
        lay.addWidget(label("Your shop data stays as it is. A safety backup is saved first, then the program updates and "
                            "opens again by itself. Windows may ask you to press Yes once." if can else
                            "This copy of the program cannot update itself. Open the download page to get the new version.",
                            "Muted", wrap=True))
        row = QHBoxLayout()
        self.skip_btn = button("Skip this version", "ghost", None, self._skip)
        self.later_btn = button("Later", "", None, self.reject)
        self.go_btn = button("Update now" if can else "Open download page", "primary", "download", self._go)
        row.addWidget(self.skip_btn)
        row.addStretch(1)
        row.addWidget(self.later_btn)
        row.addWidget(self.go_btn)
        lay.addLayout(row)
        self.bridge = _Bridge()
        self.bridge.progress.connect(self._on_progress)
        self.bridge.failed.connect(self._on_failed)
        self.bridge.ready.connect(self._on_ready)

    @staticmethod
    def _notes(text: str) -> str:
        text = text.strip() or "A new version of SDJ Mobile Care is available."
        return text[:4000]

    def _skip(self):
        settings.set_value("update_skip_version", self.latest["version"])
        self.reject()

    def _go(self):
        if not updater.can_self_install():
            webbrowser.open(self.latest.get("page") or updater.RELEASES_PAGE)
            return
        self._busy = True
        for b in (self.skip_btn, self.later_btn, self.go_btn):
            b.setEnabled(False)
        self.bar.setVisible(True)
        self.bar.setRange(0, 0)
        self.status.setText("Downloading the update…")
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        try:
            def prog(done, total):
                self.bridge.progress.emit(done, total)
                return not self._cancel
            path = updater.prepare_install(self.latest, prog)
            self.bridge.ready.emit(str(path))
        except Exception as exc:
            log.exception("update failed")
            self.bridge.failed.emit(str(exc))

    def _on_progress(self, done: int, total: int):
        if total > 0:
            self.bar.setRange(0, 100)
            self.bar.setValue(int(done * 100 / total))
            self.status.setText(f"Downloading the update… {done / 1_048_576:.0f} of {total / 1_048_576:.0f} MB")

    def _on_failed(self, msg: str):
        self._busy = False
        self.bar.setVisible(False)
        self.status.setText("")
        for b in (self.skip_btn, self.later_btn, self.go_btn):
            b.setEnabled(True)
        error(self, f"{msg}\n\nNothing was changed. You can try again later.", "Update not installed")

    def _on_ready(self, path: str):
        self.status.setText("Installing… the program will close and open again in a moment.")
        self.bar.setRange(0, 0)
        try:
            from pathlib import Path
            updater.launch_installer(Path(path))
        except Exception as exc:
            self._on_failed(str(exc))
            return
        self.accept()
        QTimer.singleShot(300, QApplication.quit)

    def reject(self):  # noqa: D401
        if self._busy:
            return                      # no closing half-way through the download
        super().reject()


class UpdateController(QObject):
    """Owns the check (in a thread) and shows the popup on the UI thread."""

    def __init__(self, parent_window=None):
        super().__init__(parent_window)
        self.window = parent_window
        self.bridge = _Bridge()
        self.bridge.checked.connect(self._on_checked)
        self._running = False

    def check(self, manual: bool = False) -> None:
        if self._running:
            return
        self._running = True
        threading.Thread(target=self._work, args=(manual,), daemon=True).start()

    def _work(self, manual: bool):
        try:
            res = updater.check()
        except Exception:
            log.exception("update check crashed")
            res = dict(state="offline", latest=None)
        try:
            updater.stage_phone_apk()          # the matching phone app, for phones to pick up at sync
        except Exception:
            log.exception("phone app copy failed")
        self.bridge.checked.emit(res, manual)

    def _on_checked(self, res: dict, manual: bool):
        self._running = False
        state, latest = res.get("state"), res.get("latest")
        if state == "new" and latest:
            if not _is_owner():
                if manual:
                    info(self.window, "Update available",
                         f"Version {latest['version']} is available. Ask the owner to update the program.")
                return
            if not manual and settings.get("update_skip_version") == latest["version"]:
                return
            UpdateDialog(latest, self.window).exec()
        elif manual:
            if state == "current":
                info(self.window, "Up to date", f"You have the newest version ({APP_VERSION}).")
            else:
                info(self.window, "Could not check",
                     "The update check needs internet on this PC. Please check the connection and try again.")


_controller: UpdateController | None = None


def start_background_check(window) -> None:
    """Called once the main window is open: a quiet check a few seconds later (at most every 12 hours)."""
    global _controller
    _controller = UpdateController(window)
    if updater.should_check_now():
        QTimer.singleShot(8000, lambda: _controller.check(False))
    else:
        threading.Thread(target=lambda: updater.stage_phone_apk(), daemon=True).start()


def controller() -> UpdateController | None:
    return _controller


def check_now(window) -> None:
    global _controller
    if _controller is None:
        _controller = UpdateController(window)
    _controller.check(True)
