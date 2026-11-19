"""Preview & print the ReportLab PDFs through Qt (works with any Windows printer incl. 58/80 mm thermal)."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPainter
from PySide6.QtPrintSupport import QPrintDialog, QPrinter, QPrinterInfo
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QVBoxLayout

from ..core import settings
from .widgets import button, error

log = logging.getLogger(__name__)

try:
    from PySide6.QtPdf import QPdfDocument
    from PySide6.QtPdfWidgets import QPdfView
    HAVE_QTPDF = True
except Exception:  # pragma: no cover - fallback to the system PDF viewer
    HAVE_QTPDF = False


def open_file(path: str | Path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def open_folder(path: str | Path) -> None:
    p = Path(path)
    if sys.platform.startswith("win") and p.is_file():
        subprocess.Popen(["explorer", "/select,", str(p)])
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(p if p.is_dir() else p.parent)))


def available_printers() -> list[str]:
    return [p.printerName() for p in QPrinterInfo.availablePrinters()]


def _make_printer() -> QPrinter:
    printer = QPrinter(QPrinter.HighResolution)
    name = settings.get("printer_name") or ""
    if name and name in available_printers():
        printer.setPrinterName(name)
    return printer


def print_pdf(path: str | Path, parent=None, ask: bool = True) -> bool:
    """Render each PDF page onto the printer at printer resolution."""
    path = str(path)
    if not HAVE_QTPDF:
        if sys.platform.startswith("win"):
            os.startfile(path, "print")  # type: ignore[attr-defined]
            return True
        open_file(path)
        return True
    doc = QPdfDocument()
    doc.load(path)
    if doc.status() != QPdfDocument.Status.Ready or doc.pageCount() == 0:
        error(parent, "Could not open the PDF for printing.")
        return False
    printer = _make_printer()
    if ask:
        dlg = QPrintDialog(printer, parent)
        dlg.setWindowTitle("Print")
        if dlg.exec() != QDialog.Accepted:
            return False
    painter = QPainter()
    if not painter.begin(printer):
        error(parent, "The printer could not be started. Check that it is connected and switched on.")
        return False
    try:
        dpi = min(printer.resolution(), 300)
        first = True
        for i in range(doc.pageCount()):
            size_pt = doc.pagePointSize(i)
            img = doc.render(i, QSize(int(size_pt.width() / 72 * dpi), int(size_pt.height() / 72 * dpi)))
            target = painter.viewport()
            fit_page = min(target.width() / img.width(), target.height() / img.height())
            fit_width = target.width() / img.width()
            # A4 pages fit the page; long thermal receipts are printed at full width and continue on the next page
            scale = fit_page if img.height() * fit_width <= target.height() * 1.02 else fit_width
            slice_h = int(target.height() / scale)
            y = 0
            while y < img.height():
                if not first:
                    printer.newPage()
                first = False
                part = img.copy(0, y, img.width(), min(slice_h, img.height() - y))
                painter.drawImage(QRectF(0, 0, part.width() * scale, part.height() * scale), part)
                y += slice_h
    finally:
        painter.end()
    return True


class PdfPreviewDialog(QDialog):
    def __init__(self, path: str | Path, parent=None, title: str = "Preview"):
        super().__init__(parent)
        self.path = str(path)
        self.setWindowTitle(title)
        self.resize(820, 900)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        if HAVE_QTPDF:
            self.doc = QPdfDocument(self)
            self.doc.load(self.path)
            view = QPdfView(self)
            view.setDocument(self.doc)
            view.setPageMode(QPdfView.PageMode.MultiPage)
            view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
            lay.addWidget(view, 1)
        else:
            lay.addWidget(QLabel(f"Saved: {self.path}"))
        bar = QHBoxLayout()
        bar.addWidget(QLabel(Path(self.path).name))
        bar.addStretch(1)
        bar.addWidget(button("Open folder", "", "download", lambda: open_folder(self.path)))
        bar.addWidget(button("Open in PDF viewer", "", "file-text", lambda: open_file(self.path)))
        pb = button("Print", "primary", "printer", self._print)
        bar.addWidget(pb)
        bar.addWidget(button("Close", "", None, self.accept))
        lay.addLayout(bar)
        pb.setFocus()

    def _print(self):
        if print_pdf(self.path, self):
            self.accept()


def show_pdf(path: str | Path, parent=None, title: str = "Preview") -> None:
    PdfPreviewDialog(path, parent, title).exec()


def deliver_pdf(path: str | Path, parent=None, title: str = "Document") -> None:
    """Follow the 'after saving' print preference from Settings."""
    mode = settings.get("print_after_save") or "ASK"
    if mode == "PRINT":
        print_pdf(path, parent, ask=False)
    elif mode == "PREVIEW" or mode == "ASK":
        show_pdf(path, parent, title)
