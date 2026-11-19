"""Reusable widgets: cards, KPI tiles, money inputs, searchable tables, pickers, report view, dialogs."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Callable

from PySide6.QtCore import (QAbstractTableModel, QDate, QModelIndex, QSortFilterProxyModel, QStringListModel, Qt,
                            QTimer, Signal)
from PySide6.QtGui import QColor, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QCompleter, QDateEdit, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
                               QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea,
                               QSizePolicy, QSpinBox, QTableView, QTextEdit, QVBoxLayout, QWidget)

from ..core.errors import BusinessError
from ..db.base import D, q2
from ..output.fmt import format_value, inr
from . import icons
from .theme import current


def new_ref() -> str:
    return uuid.uuid4().hex


# ================================================================ small helpers
def button(text: str, variant: str = "", icon_name: str | None = None, slot: Callable | None = None,
           tip: str = "") -> QPushButton:
    b = QPushButton(text.replace("&", "&&"))
    if variant:
        b.setProperty("variant", variant)
    if icon_name:
        col = current().on_accent if variant == "primary" else (current().bad if variant == "danger" else current().text2)
        b.setIcon(icons.icon(icon_name, col, 16))
    if slot:
        b.clicked.connect(lambda *_: slot())
    if tip:
        b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    return b


def label(text: str = "", obj: str = "", wrap: bool = False) -> QLabel:
    lb = QLabel(text)
    if obj:
        lb.setObjectName(obj)
    lb.setWordWrap(wrap)
    return lb


def hline() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setStyleSheet(f"color: {current().border}; background: {current().border}; max-height: 1px;")
    return f


def hbox(*widgets, spacing: int = 8, margins=(0, 0, 0, 0), stretch_at: int | None = None) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for i, w in enumerate(widgets):
        if w is None:
            lay.addStretch(1)
        elif isinstance(w, int):
            lay.addSpacing(w)
        elif hasattr(w, "addWidget") and not isinstance(w, QWidget):
            lay.addLayout(w)
        else:
            lay.addWidget(w)
    if stretch_at is not None:
        lay.insertStretch(stretch_at, 1)
    return lay


def vbox(*widgets, spacing: int = 8, margins=(0, 0, 0, 0)) -> QVBoxLayout:
    lay = QVBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for w in widgets:
        if w is None:
            lay.addStretch(1)
        elif hasattr(w, "addWidget") and not isinstance(w, QWidget):
            lay.addLayout(w)
        else:
            lay.addWidget(w)
    return lay


# ================================================================ message boxes
def info(parent, title: str, text: str) -> None:
    QMessageBox.information(parent, title, text)


def warn(parent, title: str, text: str) -> None:
    QMessageBox.warning(parent, title, text)


def error(parent, text: str, title: str = "Could not complete") -> None:
    QMessageBox.critical(parent, title, text)


def confirm(parent, title: str, text: str, yes: str = "Yes", no: str = "Cancel", danger: bool = False) -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Warning if danger else QMessageBox.Question)
    y = box.addButton(yes, QMessageBox.AcceptRole)
    box.addButton(no, QMessageBox.RejectRole)
    box.setDefaultButton(y)
    box.exec()
    return box.clickedButton() is y


def ask_text(parent, title: str, prompt: str, default: str = "") -> str | None:
    text, ok = QInputDialog.getText(parent, title, prompt, QLineEdit.Normal, default)
    return text.strip() if ok else None


def run_safely(parent, fn: Callable, *args, success: str | None = None, topic: str | None = None, **kwargs):
    """Call a business function; show friendly errors; refresh screens on success."""
    from .bus import changed, toast
    try:
        result = fn(*args, **kwargs)
    except BusinessError as exc:
        error(parent, str(exc))
        return None
    except Exception as exc:  # unexpected – logged by the global hook as well
        import logging
        logging.getLogger("sdj.ui").exception("operation failed")
        error(parent, f"Something went wrong: {exc}\n\nYour data is safe – the change was not saved.", "Unexpected error")
        return None
    if topic:
        changed(topic)
    if success:
        toast(success)
    return result if result is not None else True


def save_file_dialog(parent, title: str, default_path: str, filt: str) -> str | None:
    path, _ = QFileDialog.getSaveFileName(parent, title, default_path, filt)
    return path or None


# ================================================================ cards
class Card(QFrame):
    def __init__(self, title: str = "", hint: str = "", parent=None, margins=(16, 14, 16, 16)):
        super().__init__(parent)
        self.setObjectName("Card")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(*margins)
        self.lay.setSpacing(10)
        self.header = QHBoxLayout()
        self.header.setSpacing(8)
        if title:
            self.title_label = label(title, "SectionTitle")
            self.header.addWidget(self.title_label)
            if hint:
                h = label(hint, "SectionHint")
                self.header.addWidget(h)
            self.header.addStretch(1)
            self.lay.addLayout(self.header)

    def add(self, w):
        if isinstance(w, QWidget):
            self.lay.addWidget(w)
        else:
            self.lay.addLayout(w)
        return w


class KpiCard(QFrame):
    clicked = Signal()

    def __init__(self, title: str, icon_name: str = "indian-rupee", parent=None, compact: bool = False):
        super().__init__(parent)
        self.setObjectName("KpiCard")
        self.setMinimumWidth(120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 11 if compact else 13, 14, 11 if compact else 13)
        lay.setSpacing(12)
        self.bubble = QLabel()
        self.bubble.setObjectName("IconBubble")
        self.bubble.setFixedSize(34, 34)
        self.bubble.setAlignment(Qt.AlignCenter)
        self.icon_name = icon_name
        self.bubble.setPixmap(icons.pixmap(icon_name, current().accent_text, 18))
        lay.addWidget(self.bubble, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(1)
        self.title = label(title, "KpiTitle")
        self.value = label("—", "KpiValue")
        self._pt = 13.5 if compact else 17
        self.value.setStyleSheet(f"font-size: {self._pt}pt;")
        self.sub = label("", "KpiSub")
        for w in (self.title, self.sub, self.value):
            w.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            w.setMinimumWidth(0)
        self.sub.setWordWrap(True)
        col.addWidget(self.title)
        col.addWidget(self.value)
        col.addWidget(self.sub)
        lay.addLayout(col, 1)

    def set(self, value: str, sub: str = "", tone: str = "") -> None:
        self.value.setText(value)
        self.sub.setText(sub)
        self.sub.setVisible(bool(sub))
        p = current()
        color = {"good": p.good, "bad": p.bad, "warn": p.warn}.get(tone, p.text)
        self.value.setStyleSheet(f"color: {color}; font-size: {self._pt}pt;")
        self.setToolTip(f"{self.title.text()}: {value}" + (f"\n{sub}" if sub else ""))

    def mousePressEvent(self, e):  # noqa: N802
        self.clicked.emit()
        super().mousePressEvent(e)


def money_tone(v) -> str:
    v = D(v)
    return "good" if v > 0 else ("bad" if v < 0 else "")


# ================================================================ inputs
class MoneySpin(QDoubleSpinBox):
    def __init__(self, parent=None, maximum: float = 99_99_99_999.0):
        super().__init__(parent)
        self.setPrefix("₹ ")
        self.setDecimals(2)
        self.setRange(0, maximum)
        self.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.setMinimumWidth(110)
        self.setKeyboardTracking(True)

    def focusInEvent(self, e):  # noqa: N802
        super().focusInEvent(e)
        QTimer.singleShot(0, self.selectAll)

    def value_d(self) -> Decimal:
        return q2(str(round(self.value(), 2)))

    def set_d(self, v) -> None:
        self.setValue(float(D(v)))


class QtySpin(QSpinBox):
    def __init__(self, parent=None, minimum: int = 1, maximum: int = 99999):
        super().__init__(parent)
        self.setRange(minimum, maximum)
        self.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.setMinimumWidth(70)

    def focusInEvent(self, e):  # noqa: N802
        super().focusInEvent(e)
        QTimer.singleShot(0, self.selectAll)


class DateEdit(QDateEdit):
    def __init__(self, d: date | None = None, parent=None):
        super().__init__(parent)
        self.setCalendarPopup(True)
        self.setDisplayFormat("dd MMM yyyy")
        self.set_date(d or date.today())

    def set_date(self, d: date) -> None:
        self.setDate(QDate(d.year, d.month, d.day))

    def value(self) -> date:
        q = self.date()
        return date(q.year(), q.month(), q.day())


class ModeButtons(QWidget):
    """Segmented payment-mode selector."""
    changed = Signal(str)

    def __init__(self, modes=("CASH", "UPI", "CARD", "OTHER"), labels=None, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self._btns: dict[str, QPushButton] = {}
        labels = labels or {"CASH": "Cash", "UPI": "UPI", "CARD": "Card", "OTHER": "Other"}
        icon_for = {"CASH": "banknote", "UPI": "qr-code", "CARD": "credit-card", "OTHER": "wallet"}
        for m in modes:
            b = QPushButton(labels.get(m, m))
            b.setProperty("variant", "seg")
            b.setCheckable(True)
            b.setIcon(icons.icon(icon_for.get(m, "wallet"), current().text2, 16))
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, mm=m: self.set(mm))
            lay.addWidget(b)
            self._btns[m] = b
        self.set(modes[0])

    def set(self, mode: str) -> None:
        for m, b in self._btns.items():
            b.setChecked(m == mode)
        self._mode = mode
        self.changed.emit(mode)

    def value(self) -> str:
        return self._mode


# ================================================================ tables
@dataclass
class Col:
    key: str
    title: str
    kind: str = "text"              # text/int/money/date/datetime/pct/status/bool
    width: int | None = None
    color: Callable[[dict], str | None] | None = None
    bold: bool = False
    fmt: Callable[[dict], str] | None = None


STATUS_TONES = {
    "OUT": "bad", "LOW": "warn", "OK": "good", "CANCELLED": "bad", "VOID": "bad", "COMPLETED": "good", "PAID": "good",
    "ACTIVE": "good", "DELIVERED": "good", "READY": "info", "RECEIVED": "", "INSPECTION": "", "WAITING_PARTS": "warn",
    "IN_PROGRESS": "info", "SOLD": "info", "IN_STOCK": "good", "DAMAGED": "bad", "REMOVED": "bad", "OPEN": "warn",
    "CLOSED": "good", "REOPENED": "warn", "CREDIT": "warn", "PARTIAL": "warn",
}


class RowsModel(QAbstractTableModel):
    def __init__(self, cols: list[Col], parent=None):
        super().__init__(parent)
        self.cols = cols
        self.rows: list[dict] = []

    def set_rows(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self.cols)

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802
        if orientation == Qt.Horizontal:
            c = self.cols[section]
            if role == Qt.DisplayRole:
                return c.title
            if role == Qt.TextAlignmentRole:
                return int((Qt.AlignRight if c.kind in ("money", "int", "pct") else Qt.AlignLeft) | Qt.AlignVCenter)
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        c = self.cols[index.column()]
        v = row.get(c.key)
        if role == Qt.DisplayRole:
            if c.fmt:
                return c.fmt(row)
            if c.kind == "bool":
                return "Yes" if v else "No"
            if c.kind == "status":
                lbl = row.get(c.key + "_label")
                return str(lbl) if lbl else str(v or "").replace("_", " ").title()
            return format_value(v, c.kind) if v is not None else ""
        if role == Qt.UserRole:
            if isinstance(v, Decimal):
                return float(v)
            if isinstance(v, (date, datetime)):
                return v.isoformat()
            if v is None:
                return ""
            return v
        if role == Qt.UserRole + 1:
            return row
        if role == Qt.TextAlignmentRole:
            return int((Qt.AlignRight if c.kind in ("money", "int", "pct") else Qt.AlignLeft) | Qt.AlignVCenter)
        if role == Qt.ForegroundRole:
            p = current()
            tone = None
            if c.color:
                tone = c.color(row)
            elif c.kind == "status":
                tone = STATUS_TONES.get(str(v), None)
            elif c.kind == "money" and isinstance(v, Decimal) and v < 0:
                tone = "bad"
            if tone:
                return QColor({"good": p.good, "bad": p.bad, "warn": p.warn, "info": p.info,
                               "muted": p.muted}.get(tone, tone))
        if role == Qt.FontRole and (c.bold or c.kind == "status"):
            f = QFont()
            f.setBold(True)
            return f
        return None


class _Proxy(QSortFilterProxyModel):
    def lessThan(self, left, right):  # noqa: N802
        a, b = left.data(Qt.UserRole), right.data(Qt.UserRole)
        try:
            return a < b
        except TypeError:
            return str(a) < str(b)


class DataTable(QWidget):
    activated = Signal(dict)
    selection_changed = Signal()

    def __init__(self, cols: list[Col], parent=None, search: bool = True, placeholder: str = "Search…",
                 stretch: str | None = None, multi: bool = False, sortable: bool = True, stretch_min: int = 200):
        super().__init__(parent)
        self.cols = cols
        self._stretch_idx = next((i for i, c in enumerate(cols) if stretch and c.key == stretch), None)
        self._stretch_min = stretch_min
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.search = None
        self.top = QHBoxLayout()
        self.top.setSpacing(8)
        if search:
            self.search = QLineEdit()
            self.search.setObjectName("SearchBox")
            self.search.setPlaceholderText(placeholder)
            self.search.setClearButtonEnabled(True)
            self.search.addAction(icons.icon("search", current().muted, 16), QLineEdit.LeadingPosition)
            self.search.setMaximumWidth(380)
            self.search.setMinimumWidth(300)
            self.top.addWidget(self.search)
        self.top.addStretch(1)
        lay.addLayout(self.top)
        self.model = RowsModel(cols, self)
        self.proxy = _Proxy(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.proxy.setFilterKeyColumn(-1)
        self.view = QTableView()
        self.view.setModel(self.proxy)
        self.view.setSortingEnabled(sortable)
        self.view.setAlternatingRowColors(True)
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.ExtendedSelection if multi else QAbstractItemView.SingleSelection)
        self.view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.view.verticalHeader().setVisible(False)
        self.view.verticalHeader().setDefaultSectionSize(34)
        self.view.setShowGrid(False)
        self.view.setWordWrap(False)
        hh = self.view.horizontalHeader()
        hh.setHighlightSections(False)
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(QHeaderView.Interactive)
        minimum = {"date": 116, "datetime": 172, "money": 100, "status": 110}
        for i, c in enumerate(cols):
            if c.width or c.kind in minimum:
                self.view.setColumnWidth(i, max(c.width or 0, minimum.get(c.kind, 0)))
        if self._stretch_idx is None:
            hh.setStretchLastSection(True)
        self.view.viewport().installEventFilter(self)
        lay.addWidget(self.view, 1)
        self.footer = label("", "Muted")
        self.footer.setVisible(False)
        lay.addWidget(self.footer)
        if self.search:
            self.search.textChanged.connect(self.proxy.setFilterFixedString)
        self.view.doubleClicked.connect(self._on_double)
        self.view.selectionModel().selectionChanged.connect(lambda *_: self.selection_changed.emit())

    def eventFilter(self, obj, ev):  # noqa: N802
        from PySide6.QtCore import QEvent
        if ev.type() == QEvent.Resize and self._stretch_idx is not None:
            self._fit_columns()
        return False

    def _fit_columns(self) -> None:
        idx = self._stretch_idx
        fixed = sum(self.view.columnWidth(i) for i in range(len(self.cols)) if i != idx)
        want = max(self.view.viewport().width() - fixed - 2, self._stretch_min)
        if abs(self.view.columnWidth(idx) - want) > 1:
            self.view.setColumnWidth(idx, want)

    def set_rows(self, rows: list[dict]) -> None:
        sel = self.selected()
        key = sel.get("id") if sel else None
        self.model.set_rows(rows)
        if key is not None:
            for r in range(self.proxy.rowCount()):
                if self.proxy.index(r, 0).data(Qt.UserRole + 1).get("id") == key:
                    self.view.selectRow(r)
                    break

    def rows(self) -> list[dict]:
        return self.model.rows

    def visible_rows(self) -> list[dict]:
        return [self.proxy.index(r, 0).data(Qt.UserRole + 1) for r in range(self.proxy.rowCount())]

    def selected(self) -> dict | None:
        idx = self.view.selectionModel().selectedRows()
        if not idx:
            return None
        return idx[0].data(Qt.UserRole + 1)

    def selected_all(self) -> list[dict]:
        return [i.data(Qt.UserRole + 1) for i in self.view.selectionModel().selectedRows()]

    def _on_double(self, index):
        self.activated.emit(index.data(Qt.UserRole + 1))

    def set_footer(self, text: str) -> None:
        self.footer.setText(text)
        self.footer.setVisible(bool(text))

    def add_top(self, w) -> None:
        self.top.addWidget(w)

    def export(self, parent, title: str = "Export") -> None:
        from .. import config
        from ..output.excel_export import rows_to_excel
        path = save_file_dialog(parent, title, str(config.exports_dir() / f"{title.replace(' ', '_')}.xlsx"),
                                "Excel (*.xlsx)")
        if not path:
            return
        rows = self.visible_rows()
        headers = [c.title for c in self.cols]
        data = [[(c.fmt(r) if c.fmt else r.get(c.key)) for c in self.cols] for r in rows]
        money = {i for i, c in enumerate(self.cols) if c.kind == "money"}
        run_safely(parent, rows_to_excel, path, title, headers, data, money, success=f"Exported {len(rows)} rows")


# ================================================================ date range bar
class DateRangeBar(QWidget):
    changed = Signal(object, object)

    PRESETS = ["Today", "Yesterday", "This week", "This month", "Last month", "Last 30 days", "This year",
               "Financial year", "Custom"]

    def __init__(self, default: str = "This month", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.combo = QComboBox()
        self.combo.addItems(self.PRESETS)
        self.start = DateEdit()
        self.end = DateEdit()
        lay.addWidget(icon_label("calendar"))
        lay.addWidget(self.combo)
        lay.addWidget(self.start)
        lay.addWidget(QLabel("to"))
        lay.addWidget(self.end)
        self.combo.currentTextChanged.connect(self._preset)
        self.start.dateChanged.connect(self._custom)
        self.end.dateChanged.connect(self._custom)
        self._busy = False
        self.combo.setCurrentText(default)
        self._preset(default)

    def _preset(self, name: str) -> None:
        t = date.today()
        if name == "Today":
            a = b = t
        elif name == "Yesterday":
            a = b = t - timedelta(days=1)
        elif name == "This week":
            a, b = t - timedelta(days=t.weekday()), t
        elif name == "This month":
            a, b = t.replace(day=1), t
        elif name == "Last month":
            b = t.replace(day=1) - timedelta(days=1)
            a = b.replace(day=1)
        elif name == "Last 30 days":
            a, b = t - timedelta(days=29), t
        elif name == "This year":
            a, b = date(t.year, 1, 1), t
        elif name == "Financial year":
            a = date(t.year if t.month >= 4 else t.year - 1, 4, 1)
            b = t
        else:
            return
        self._busy = True
        self.start.set_date(a)
        self.end.set_date(b)
        self._busy = False
        self.changed.emit(a, b)

    def _custom(self) -> None:
        if self._busy:
            return
        if self.combo.currentText() != "Custom":
            self._busy = True
            self.combo.setCurrentText("Custom")
            self._busy = False
        a, b = self.range()
        if a > b:
            return
        self.changed.emit(a, b)

    def range(self) -> tuple[date, date]:
        return self.start.value(), self.end.value()


def icon_label(name: str, size: int = 16, color: str | None = None) -> QLabel:
    lb = QLabel()
    lb.setPixmap(icons.pixmap(name, color or current().muted, size))
    return lb


# ================================================================ pickers
class ProductPicker(QLineEdit):
    """Type to search by name / brand / code, or scan a barcode and press Enter."""
    picked = Signal(dict)

    def __init__(self, parent=None, group: str | None = None, in_stock_only: bool = False, exclude_imei: bool = False,
                 placeholder: str = "Search product name / code, or scan barcode…"):
        super().__init__(parent)
        self.group, self.in_stock_only, self.exclude_imei = group, in_stock_only, exclude_imei
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)
        self.addAction(icons.icon("scan-barcode", current().muted, 16), QLineEdit.LeadingPosition)
        self._map: dict[str, dict] = {}
        self._model = QStringListModel(self)
        comp = QCompleter(self._model, self)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.setFilterMode(Qt.MatchContains)
        comp.setMaxVisibleItems(12)
        self.setCompleter(comp)
        comp.activated.connect(self._chosen)
        self.returnPressed.connect(self._enter)
        self.reload()

    def reload(self) -> None:
        from ..core.inventory import list_products
        rows = list_products(group=self.group, in_stock_only=self.in_stock_only)
        if self.exclude_imei:
            rows = [r for r in rows if not r["track_imei"]]
        self._map = {}
        for r in rows:
            txt = (f"{r['display']}  ·  {r['code']}  ·  Stock {r['quantity']}  ·  {inr(r['selling_price'])}")
            self._map[txt] = r
        self._model.setStringList(list(self._map.keys()))

    def _chosen(self, text: str) -> None:
        r = self._map.get(text)
        if r:
            QTimer.singleShot(0, self.clear)
            self.picked.emit(r)

    def _enter(self) -> None:
        text = self.text().strip()
        if not text:
            return
        if text in self._map:
            self._chosen(text)
            return
        from ..core.inventory import find_imei_in_stock, find_product, get_product
        p = find_product(text)
        if p:
            self.clear()
            self.picked.emit(p)
            return
        imei = find_imei_in_stock(text)
        if imei:
            p = get_product(imei["product_id"])
            p["_imei"] = imei
            self.clear()
            self.picked.emit(p)
            return
        matches = [k for k in self._map if text.lower() in k.lower()]
        if len(matches) == 1:
            self._chosen(matches[0])
        else:
            self.completer().complete()


class CustomerPicker(QWidget):
    """Search existing customers by name/phone; default is Walk-in."""
    changed = Signal(object)

    def __init__(self, parent=None, allow_walk_in: bool = True):
        super().__init__(parent)
        self.allow_walk_in = allow_walk_in
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Customer phone or name (blank = Walk-in)")
        self.edit.setClearButtonEnabled(True)
        self.edit.addAction(icons.icon("user", current().muted, 16), QLineEdit.LeadingPosition)
        self._model = QStringListModel(self)
        comp = QCompleter(self._model, self)
        comp.setCaseSensitivity(Qt.CaseInsensitive)
        comp.setFilterMode(Qt.MatchContains)
        self.edit.setCompleter(comp)
        comp.activated.connect(self._chosen)
        self.edit.textEdited.connect(self._typed)
        self.add_btn = button("", "", "user-plus", self._add, "Add new customer")
        self.add_btn.setFixedWidth(40)
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.add_btn)
        self._map: dict[str, dict] = {}
        self.customer: dict | None = None
        self.reload()

    def reload(self) -> None:
        from ..core.parties import list_customers
        self._map = {f"{c['name']}  ·  {c['phone']}" if c["phone"] else c["name"]: c for c in list_customers()}
        self._model.setStringList(list(self._map.keys()))

    def _chosen(self, text: str) -> None:
        c = self._map.get(text)
        if c:
            self.set_customer(c)

    def _typed(self, _t: str) -> None:
        if self.customer is not None:
            self.customer = None
            self.changed.emit(None)

    def set_customer(self, c: dict | None) -> None:
        self.customer = c
        self.edit.setText(f"{c['name']}  ·  {c['phone']}" if c and c.get("phone") else (c["name"] if c else ""))
        self.changed.emit(c)

    def _add(self) -> None:
        from .dialogs import CustomerDialog
        text = self.edit.text().strip()
        pre = {"phone": text} if text.replace("+", "").isdigit() else {"name": text}
        dlg = CustomerDialog(self, pre)
        if dlg.exec() == QDialog.Accepted and dlg.result_id:
            self.reload()
            from ..core.parties import get_customer
            self.set_customer(get_customer(dlg.result_id))

    def customer_id(self) -> int | None:
        return self.customer["id"] if self.customer else None


# ================================================================ form dialog base
class FormDialog(QDialog):
    def __init__(self, parent, title: str, width: int = 480):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(width)
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(18, 16, 18, 14)
        self.root.setSpacing(12)
        self.form = QFormLayout()
        self.form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.form.setHorizontalSpacing(12)
        self.form.setVerticalSpacing(9)
        self.root.addLayout(self.form)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok_btn = self.buttons.button(QDialogButtonBox.Ok)
        self.ok_btn.setProperty("variant", "primary")
        self.ok_btn.setText("Save")
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        self.result_id = None

    def finish_layout(self) -> None:
        self.root.addStretch(1)
        self.root.addWidget(self.buttons)

    def _accept(self) -> None:
        try:
            if self.on_save() is not False:
                self.accept()
        except BusinessError as exc:
            error(self, str(exc))

    def on_save(self):  # override
        return True


# ================================================================ report view
class ReportView(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self._host = QWidget()
        self._host.setObjectName("Transparent")
        self._lay = QVBoxLayout(self._host)
        self._lay.setContentsMargins(0, 0, 6, 0)
        self._lay.setSpacing(12)
        self.setWidget(self._host)
        self.report = None

    def set_report(self, report) -> None:
        self.report = report
        while self._lay.count():
            item = self._lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        p = current()
        for sec in report.sections:
            card = Card(sec.title)
            if sec.kvs:
                grid = QGridLayout()
                grid.setHorizontalSpacing(18)
                grid.setVerticalSpacing(6)
                ncol = max(1, sec.kv_columns)
                for i, kv in enumerate(sec.kvs):
                    r, c = divmod(i, ncol)
                    lb = QLabel(kv.label)
                    lb.setStyleSheet(f"color: {p.text if kv.strong else p.text2};" + ("font-weight:700;" if kv.strong else ""))
                    val = QLabel(format_value(kv.value, kv.kind))
                    color = {"good": p.good, "bad": p.bad}.get(kv.tone, p.text)
                    val.setStyleSheet(f"color: {color}; font-weight: {'800' if kv.strong else '600'};")
                    val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    val.setTextInteractionFlags(Qt.TextSelectableByMouse)
                    grid.addWidget(lb, r, c * 2)
                    grid.addWidget(val, r, c * 2 + 1)
                for c in range(ncol):
                    grid.setColumnStretch(c * 2, 3 if ncol > 1 else 1)
                    grid.setColumnStretch(c * 2 + 1, 2 if ncol > 1 else 1)
                if ncol == 1:
                    grid.setColumnStretch(2, 2)
                card.add(grid)
            if sec.table:
                tb = sec.table
                cols = [Col(f"c{i}", c.title, c.kind if c.kind != "text" else "text", None) for i, c in enumerate(tb.columns)]
                rows = [{f"c{i}": v for i, v in enumerate(r)} for r in tb.rows]
                if tb.totals:
                    rows.append({**{f"c{i}": v for i, v in enumerate(tb.totals)}, "_total": True})
                t = DataTable(cols, search=len(rows) > 15, placeholder="Filter rows…", sortable=False)
                t.set_rows(rows)
                t.view.resizeColumnsToContents()
                for i in range(len(cols)):
                    t.view.setColumnWidth(i, min(max(t.view.columnWidth(i) + 18, 70), 320))
                t.view.horizontalHeader().setStretchLastSection(True)
                h = min(36 + 34 * max(len(rows), 1) + (44 if len(rows) > 15 else 0), 460)
                t.setMinimumHeight(h)
                t.setMaximumHeight(h + 10)
                card.add(t)
            if sec.note:
                card.add(label(sec.note, "Muted", wrap=True))
            self._lay.addWidget(card)
        self._lay.addStretch(1)


def add_shortcut(widget: QWidget, seq: str, fn: Callable) -> QShortcut:
    sc = QShortcut(QKeySequence(seq), widget)
    sc.setContext(Qt.WidgetWithChildrenShortcut)
    sc.activated.connect(fn)
    return sc


class Page(QWidget):
    """Base class for sidebar pages: lazy refresh when data changes."""
    title = ""
    subtitle = ""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PageRoot")
        self._dirty = True
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(22, 18, 22, 18)
        self.root.setSpacing(14)

    def mark_dirty(self) -> None:
        self._dirty = True
        if self.isVisible():
            QTimer.singleShot(60, self._refresh_if_dirty)

    def _refresh_if_dirty(self) -> None:
        if self._dirty and self.isVisible():
            self._dirty = False
            self.refresh()

    def showEvent(self, e):  # noqa: N802
        super().showEvent(e)
        QTimer.singleShot(0, self._refresh_if_dirty)

    def refresh(self) -> None:  # override
        pass

    def open_with(self, payload: Any) -> None:  # override for navigation payloads
        pass


def scroll_wrap(widget: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.NoFrame)
    widget.setObjectName("Transparent")
    sa.setWidget(widget)
    return sa


def multiline(placeholder: str = "", height: int = 64) -> QTextEdit:
    t = QTextEdit()
    t.setAcceptRichText(False)
    t.setPlaceholderText(placeholder)
    t.setFixedHeight(height)
    return t
