"""`SDJMobileCare.exe --self-test` – verifies an installed/built copy without touching real data.

Runs in a temporary folder: creates a database, a product, a sale, a repair job, an invoice PDF (A4 + thermal),
a closing report PDF, an Excel export and a backup. Exit code 0 = everything works.
A report is written to %TEMP%/sdj_selftest.txt (the windowed .exe has no console).
"""
from __future__ import annotations

import os
import tempfile
import traceback
from decimal import Decimal
from pathlib import Path


def run_self_test() -> int:
    report_path = Path(tempfile.gettempdir()) / "sdj_selftest.txt"
    lines = []

    def ok(msg):
        lines.append(f"[OK] {msg}")

    try:
        tmp = Path(tempfile.mkdtemp(prefix="sdj_selftest_"))
        from . import APP_VERSION, config
        config.set_data_dir(tmp)
        ok(f"SDJ Mobile Care {APP_VERSION}; assets at {config.asset_path()}")
        for name in ("logo.png", "app.ico", "fonts/DejaVuSans.ttf", "icons/layout-dashboard.svg"):
            if not config.asset_path(*name.split("/")).exists():
                raise RuntimeError(f"missing asset {name}")
        ok("assets present")
        from .db.init_db import open_database
        open_database(config.db_path())
        ok("database created")
        from .core import auth, closing, inventory, repairs, sales
        from .core.sales import CartItem
        auth.create_user("selftest", "selftest1", "ADMIN", check_perm=False)
        auth.set_current_user(auth.authenticate("selftest", "selftest1"))
        cats = {c["name"]: c["id"] for c in inventory.list_categories()}
        pid = inventory.create_product(dict(name="Test Charger", category_id=cats["Chargers"], purchase_price=250,
                                            selling_price=400), 10, 250)
        part = inventory.create_product(dict(name="Test Display", category_id=cats["Displays"], purchase_price=800,
                                             selling_price=1000), 2, 800)
        s = sales.create_sale([CartItem(pid, 2, Decimal("400"))], payments={"CASH": Decimal("800")})
        assert inventory.get_product(pid)["quantity"] == 8
        ok("sale saved, stock deducted 10 → 8")
        job = repairs.create_job(dict(customer_name="Test", customer_phone="9000000000", brand="Test", model="X",
                                      problem="Screen", labour_charge=500))
        repairs.add_part(job["id"], part, 1, 1000)
        repairs.deliver_job(job["id"], 1500, "CASH")
        ok("repair job delivered")
        from .output.pdf_docs import invoice_pdf, job_pdf
        d = sales.get_sale(s["id"])
        for fmt in ("A4", "THERMAL_80", "THERMAL_58"):
            p = invoice_pdf(d, fmt, tmp / f"inv_{fmt}.pdf")
            assert p.read_bytes()[:4] == b"%PDF"
        job_pdf(repairs.get_job(job["id"]), "RECEIPT", "THERMAL_80", tmp / "job.pdf")
        ok("invoice / receipt / service bill PDFs")
        from datetime import date
        res = closing.close_day(date.today(), actual_cash=Decimal("2300"), opening_cash=0, make_backup=True)
        assert res["pdf_path"] and Path(res["pdf_path"]).exists(), res.get("pdf_error")
        rep = closing.saved_report(date.today())
        assert rep.kv("Financial report", "Net operating profit") == Decimal("1000.00"), rep.kv("Financial report", "Net operating profit")
        ok("daily closing + PDF + backup; net operating profit ₹1,000.00 as expected")
        from .output.excel_export import render_report_excel
        render_report_excel(rep, tmp / "closing.xlsx")
        ok("Excel export")
        try:
            from PySide6.QtPdf import QPdfDocument  # noqa: F401
            ok("Qt PDF preview module available")
        except Exception as exc:  # printing falls back to the system PDF viewer
            lines.append(f"[WARN] Qt PDF module not available ({exc}); printing uses the default PDF viewer")
        # ---- GUI: build the main window offscreen and open every screen (pages load lazily)
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = None
        try:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance() or QApplication(["sdj-selftest"])
        except Exception as exc:
            lines.append(f"[WARN] GUI check skipped – could not start Qt offscreen ({exc})")
        if app is not None:
            from PySide6.QtWidgets import QDialog, QMessageBox
            failures = []
            for name in ("critical", "warning", "information"):
                setattr(QMessageBox, name, staticmethod(lambda *a, **k: failures.append(a) or QMessageBox.Ok))
            QDialog.exec = lambda self: 0
            from .ui.main_window import PAGES, MainWindow
            from .ui.theme import stylesheet
            app.setStyleSheet(stylesheet())
            win = MainWindow()
            win.resize(1366, 768)
            for key, title, *_ in PAGES:
                win.navigate(key)
                page = win.pages.get(key)
                if page is None:
                    raise RuntimeError(f"screen '{title}' could not be opened")
                page.refresh()
                app.processEvents()
            if failures:
                raise RuntimeError(f"error dialogs while opening screens: {failures}")
            win.close()
            ok(f"all {len(PAGES)} screens open")
        lines.append("SELF-TEST PASSED")
        code = 0
    except Exception:
        lines.append("SELF-TEST FAILED")
        lines.append(traceback.format_exc())
        code = 1
    text = "\n".join(lines)
    try:
        report_path.write_text(text, encoding="utf-8")
    except OSError:
        pass
    try:
        print(text)
    except Exception:
        pass
    return code
