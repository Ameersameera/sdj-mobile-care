"""Daily closing: owner-controlled Close Day with full report, cash reconciliation, PDF/Excel, lock & audited reopen."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QTabWidget, QVBoxLayout, QWidget

from ... import config
from ...core import closing
from ...core.auth import current_user
from ...core.business_day import current_business_date
from ...db.base import session_scope
from ...output.fmt import d_only, dt, inr
from ..bus import changed, toast
from ..dialogs import CashCountDialog
from ..printing import open_folder, show_pdf
from ..widgets import (Card, Col, DataTable, MoneySpin, Page, ReportView, ask_text, button, confirm, error, hbox, info,
                       label, run_safely, save_file_dialog)


class DailyClosingPage(Page):
    subtitle = "Close the business day whenever you finish – stock always carries forward"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tabs = QTabWidget()
        self.root.addWidget(self.tabs, 1)
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 10, 0, 0)
        lay.setSpacing(12)
        top = Card()
        self.day = QComboBox()
        self.day.setMinimumWidth(240)
        self.day.currentIndexChanged.connect(lambda *_: self._load_preview(reset_cash=True))
        self.opening = MoneySpin()
        self.actual = MoneySpin()
        self.keep = MoneySpin()
        self.keep.setToolTip("Cash left in the drawer as tomorrow's opening cash. The rest is treated as taken out "
                             "(bank deposit / owner).")
        self._keep_touched = False
        self.keep.valueChanged.connect(self._keep_edited)
        self.taken_lbl = label("", "Muted")
        self.notes = QLineEdit()
        self.notes.setPlaceholderText("Closing notes (optional)")
        self.expected_lbl = label("", "SectionTitle")
        self.diff_lbl = label("", "SectionTitle")
        top.add(hbox(QLabel("Day to close"), self.day, 18, QLabel("Opening cash"), self.opening, 18,
                     QLabel("Actual cash counted"), self.actual, button("Count notes", "", "calculator", self.count_cash), None))
        top.add(hbox(self.expected_lbl, 24, self.diff_lbl, None, QLabel("Keep in drawer for tomorrow"), self.keep,
                     self.taken_lbl))
        top.add(hbox(self.notes))
        self.banner = label("", "BannerWarn", wrap=True)
        self.banner.setVisible(False)
        top.add(self.banner)
        self.close_btn = button("CLOSE DAY", "primary", "lock", self.close_day)
        self.close_btn.setMinimumHeight(40)
        top.add(hbox(button("Refresh figures", "", "refresh-cw", lambda: self._load_preview()),
                     button("Preview PDF", "", "file-text", self.preview_pdf),
                     button("Excel", "", "file-spreadsheet", self.export_excel), None, self.close_btn))
        lay.addWidget(top)
        self.view = ReportView()
        lay.addWidget(self.view, 1)
        self.opening.valueChanged.connect(self._schedule)
        self.actual.valueChanged.connect(self._update_cash_labels)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._load_preview)
        self.tabs.addTab(w, "Close day")
        w2 = QWidget()
        l2 = QVBoxLayout(w2)
        l2.setContentsMargins(0, 10, 0, 0)
        c2 = Card("Closed days", "locked against accidental changes")
        self.hist = DataTable([Col("business_date", "Date", "date", 105, bold=True), Col("status", "Status", "status", 90),
                               Col("total_revenue", "Revenue", "money", 115), Col("gross_profit", "Gross profit", "money", 115),
                               Col("net_profit", "Net profit", "money", 110), Col("expected_cash", "Expected cash", "money", 115),
                               Col("actual_cash", "Actual cash", "money", 110),
                               Col("difference", "Difference", "money", 100, color=lambda r: None if r["difference"] == 0 else ("good" if r["difference"] > 0 else "bad")),
                               Col("carry_forward", "Kept in drawer", "money", 115),
                               Col("closed_by", "Closed by", width=110), Col("closed_at", "Closed at", "datetime", 150)],
                              search=True, stretch="closed_by")
        self.hist.activated.connect(self.view_closed)
        c2.add(self.hist)
        btns = [button("View report", "primary", "eye", lambda: self.view_closed(self.hist.selected())),
                button("PDF", "", "file-text", self.closed_pdf), button("Excel", "", "file-spreadsheet", self.closed_excel)]
        if current_user().can("reopen_day"):
            btns.append(button("Reopen day (admin)", "danger", "rotate-ccw", self.reopen))
        c2.add(hbox(*btns, None))
        l2.addWidget(c2)
        self.tabs.addTab(w2, "Closing history")
        self.tabs.currentChanged.connect(lambda *_: self.refresh())
        self._report = None

    # ------------------------------------------------------------ helpers
    def refresh(self):
        if self.tabs.currentIndex() == 1:
            self.hist.set_rows(closing.list_closings())
            return
        with session_scope() as s:
            cur = current_business_date(s)
        st = closing.closing_status(cur)
        days = list(st["pending_before"]) + [cur]
        keep = self.day.currentData()
        self.day.blockSignals(True)
        self.day.clear()
        for d in days:
            tag = " (current)" if d == cur else " (not closed yet)"
            self.day.addItem(d.strftime("%a, %d %b %Y") + tag, d)
        idx = self.day.findData(keep) if keep in days else 0
        self.day.setCurrentIndex(max(idx, 0))
        self.day.blockSignals(False)
        self._load_preview(reset_cash=True)

    def _schedule(self):
        self._timer.start(350)

    def _selected_day(self) -> date | None:
        return self.day.currentData()

    def _load_preview(self, reset_cash: bool = False):
        d = self._selected_day()
        if d is None:
            return
        if reset_cash:
            self._keep_touched = False
            self.opening.blockSignals(True)
            self.opening.set_d(closing.suggested_opening_cash(d))
            self.opening.blockSignals(False)
        rep = closing.preview(d, self.opening.value_d(), self.actual.value_d(),
                              self.keep.value_d() if self._keep_touched else None)
        self._report = rep
        self._expected = rep.kv("Cash reconciliation", "Expected closing cash") or Decimal("0")
        if reset_cash:
            self.actual.blockSignals(True)
            self.actual.set_d(self._expected)
            self.actual.blockSignals(False)
        st = closing.closing_status(d)
        msgs = []
        if st["pending_before"] and d != st["pending_before"][0]:
            msgs.append("Earlier days are not closed yet: " + ", ".join(x.strftime('%d %b') for x in st["pending_before"])
                        + ". Close them first (select them above).")
        if st["reopened"]:
            msgs.append("This day was reopened by an admin for corrections. Close it again when done.")
        self.banner.setText(" ".join(msgs))
        self.banner.setVisible(bool(msgs))
        self.close_btn.setEnabled(not (st["pending_before"] and d != st["pending_before"][0]))
        self.view.set_report(rep)
        self._update_cash_labels()

    def _keep_edited(self):
        self._keep_touched = True
        self._update_taken()

    def _update_taken(self):
        taken = self.actual.value_d() - self.keep.value_d()
        self.taken_lbl.setText(f"Taken out: {inr(taken)}" if taken >= 0 else "Cannot keep more than counted")

    def _update_cash_labels(self):
        if not self._keep_touched:
            self.keep.blockSignals(True)
            self.keep.set_d(self.actual.value_d())
            self.keep.blockSignals(False)
        self._update_taken()
        exp = getattr(self, "_expected", Decimal("0"))
        diff = self.actual.value_d() - exp
        self.expected_lbl.setText(f"Expected closing cash: {inr(exp)}")
        self.diff_lbl.setText(f"Difference: {inr(diff)}" + (" (short)" if diff < 0 else (" (extra)" if diff > 0 else " ✓")))
        self.diff_lbl.setObjectName("BadText" if diff < 0 else ("WarnText" if diff > 0 else "GoodText"))
        self.diff_lbl.style().unpolish(self.diff_lbl)
        self.diff_lbl.style().polish(self.diff_lbl)

    def count_cash(self):
        dlg = CashCountDialog(self)
        if dlg.exec() == QDialog.Accepted:
            self.actual.set_d(dlg.total)

    # ------------------------------------------------------------ actions
    def close_day(self):
        d = self._selected_day()
        if d is None:
            return
        exp = getattr(self, "_expected", Decimal("0"))
        diff = self.actual.value_d() - exp
        text = (f"Close the business day {d.strftime('%d %b %Y')}?\n\nExpected cash: {inr(exp)}\nActual cash: "
                f"{inr(self.actual.value_d())}\nDifference: {inr(diff)}\nKept in drawer for tomorrow: {inr(self.keep.value_d())}"
                "\n\nThe day's records will be locked, a PDF report saved and a backup taken. Stock is carried forward.")
        if not confirm(self, "Close day", text, "Close day"):
            return
        res = run_safely(self, closing.close_day, d, self.actual.value_d(), self.opening.value_d(), self.notes.text(),
                         carry_forward=self.keep.value_d(), topic="closing")
        if not res:
            return
        self.notes.clear()
        msg = f"Day {d.strftime('%d %b %Y')} closed."
        if res.get("pdf_path"):
            msg += f"\n\nPDF report: {res['pdf_path']}"
        if res.get("pdf_error"):
            msg += f"\n\nThe PDF could not be created ({res['pdf_error']}); you can export it from Closing history."
        if res.get("backup"):
            msg += f"\nBackup: {res['backup']}"
        info(self, "Day closed", msg)
        if res.get("pdf_path"):
            show_pdf(res["pdf_path"], self, "Daily closing report")
        self.refresh()

    def preview_pdf(self):
        if not self._report:
            return
        from ...output.pdf_report import render_report_pdf
        d = self._selected_day()
        path = config.reports_dir() / f"Daily_Closing_{d.isoformat()}_preview.pdf"
        if run_safely(self, render_report_pdf, self._report, path):
            show_pdf(path, self, "Closing preview")

    def export_excel(self, report=None, d=None):
        report = report or self._report
        d = d or self._selected_day()
        if not report:
            return
        path = save_file_dialog(self, "Export closing report", str(config.reports_dir() / f"Daily_Closing_{d}.xlsx"), "Excel (*.xlsx)")
        if path:
            from ...output.excel_export import render_report_excel
            run_safely(self, render_report_excel, report, path, success="Excel report saved")

    def view_closed(self, row):
        if not row:
            return
        rep = closing.saved_report(row["business_date"])
        if not rep:
            error(self, "No saved report for this day.")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Closing report – {d_only(row['business_date'])}")
        dlg.resize(1150, 780)
        lay = QVBoxLayout(dlg)
        lay.addWidget(label(f"Closed by {row['closed_by']} at {dt(row['closed_at'])}" +
                            (f" · closed {row['close_count']} times" if row["close_count"] > 1 else ""), "Muted"))
        v = ReportView()
        v.set_report(rep)
        lay.addWidget(v)
        dlg.exec()

    def closed_pdf(self):
        r = self.hist.selected()
        if not r:
            return
        rep = closing.saved_report(r["business_date"])
        from ...output.pdf_report import render_report_pdf
        path = config.reports_dir() / f"Daily_Closing_{r['business_date'].isoformat()}.pdf"
        if rep and run_safely(self, render_report_pdf, rep, path):
            show_pdf(path, self, "Daily closing report")

    def closed_excel(self):
        r = self.hist.selected()
        if r:
            self.export_excel(closing.saved_report(r["business_date"]), r["business_date"])

    def reopen(self):
        r = self.hist.selected()
        if not r:
            return
        if not r["is_locked"]:
            error(self, "This day is already open.")
            return
        reason = ask_text(self, "Reopen day", f"Reopening {d_only(r['business_date'])} allows corrections and is recorded in "
                                              "the audit log.\n\nReason (required):")
        if reason:
            if run_safely(self, closing.reopen_day, r["business_date"], reason, success="Day reopened – close it again after corrections",
                          topic="closing"):
                self.refresh()
