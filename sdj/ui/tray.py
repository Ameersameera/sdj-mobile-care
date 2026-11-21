"""Background mode (v1.5.2).

When its window is closed, SDJ Mobile Care keeps running as an icon in the Windows notification area (tray): phones
keep syncing, automatic backups keep running and update notices still arrive. Click the icon to open it again (sign in
first); right-click → Quit closes it fully.
"""
from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtCore import QObject
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

log = logging.getLogger(__name__)


class _Life:
    """Set while the program is really closing (Quit, update, Windows shutting down): the window may then close."""
    quitting = False
    updating = False


life = _Life()


def available() -> bool:
    try:
        return QSystemTrayIcon.isSystemTrayAvailable()
    except Exception:
        return False


class Tray(QObject):
    def __init__(self, on_open: Callable[[], None], on_quit: Callable[[], None]):
        super().__init__()
        self.on_open = on_open
        self._on_click: Callable[[], None] | None = None
        self.icon = QSystemTrayIcon(QApplication.windowIcon(), self)
        self.icon.setToolTip("SDJ Mobile Care – phones can sync, backups are on")
        self.menu = QMenu()
        a_open = QAction("Open SDJ Mobile Care", self.menu)
        a_open.triggered.connect(lambda: self.on_open())
        a_quit = QAction("Quit (phones stop syncing)", self.menu)
        a_quit.triggered.connect(lambda: on_quit())
        self.menu.addAction(a_open)
        self.menu.addSeparator()
        self.menu.addAction(a_quit)
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self._activated)
        self.icon.messageClicked.connect(self._message_clicked)
        self.icon.show()

    def _activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.on_open()

    def notify(self, title: str, text: str, on_click: Callable[[], None] | None = None, msecs: int = 10_000) -> None:
        """A Windows notification from the tray icon; clicking it calls [on_click] (default: open the program)."""
        self._on_click = on_click or self.on_open
        try:
            self.icon.showMessage(title, text, QSystemTrayIcon.MessageIcon.Information, msecs)
        except Exception:
            log.exception("tray notification failed")

    def _message_clicked(self) -> None:
        cb, self._on_click = self._on_click, None
        if cb is not None:
            cb()

    def hide(self) -> None:
        self.icon.hide()
