"""Lucide line icons (ISC licence) recoloured at runtime."""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from .. import config


@lru_cache(maxsize=512)
def _svg(name: str) -> str:
    p = config.asset_path("icons", f"{name}.svg")
    if not p.exists():
        p = config.asset_path("icons", "circle-alert.svg")
    return p.read_text(encoding="utf-8")


@lru_cache(maxsize=1024)
def pixmap(name: str, color: str = "#1D1F15", size: int = 18, stroke: float = 2.0) -> QPixmap:
    svg = _svg(name).replace("currentColor", color).replace('stroke-width="2"', f'stroke-width="{stroke}"')
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    scale = 3
    pm = QPixmap(size * scale, size * scale)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter)
    painter.end()
    pm.setDevicePixelRatio(scale)
    return pm


def icon(name: str, color: str | None = None, size: int = 18) -> QIcon:
    from .theme import current
    return QIcon(pixmap(name, color or current().text, size))


def isize(n: int = 18) -> QSize:
    return QSize(n, n)
