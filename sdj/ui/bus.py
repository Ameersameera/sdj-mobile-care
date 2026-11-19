"""Application-wide signals so every screen refreshes automatically after a transaction."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class _Bus(QObject):
    data_changed = Signal(str)          # topic: sales / stock / service / expenses / purchases / closing / settings
    navigate = Signal(str, object)      # page key, optional payload
    theme_changed = Signal(str)
    settings_changed = Signal()
    toast = Signal(str, str)            # message, kind (info / error)


_bus: _Bus | None = None


def bus() -> _Bus:
    global _bus
    if _bus is None:
        _bus = _Bus()
    return _bus


def changed(topic: str = "all") -> None:
    bus().data_changed.emit(topic)


def toast(message: str, kind: str = "info") -> None:
    bus().toast.emit(message, kind)


def go(page: str, payload=None) -> None:
    bus().navigate.emit(page, payload)
