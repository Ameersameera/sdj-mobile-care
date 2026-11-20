"""Settings: shop details & logo, invoice/GST, printing, backup/restore, data reset, stock & service, product
categories, users & owner approval, audit log."""
from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QRadioButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from ... import APP_TITLE, APP_VERSION, config
from ...core import audit, auth, backup, inventory, reset, settings
from ...core.auth import ROLE_LABELS, ROLES, current_user
from ...output.fmt import dt
from ..bus import bus, changed, toast
from ..dialogs import CategoryDialog
from ..printing import available_printers, open_folder, show_pdf
from ..theme import current
from ..widgets import (Card, Col, DataTable, DateEdit, FormDialog, MoneySpin, Page, button, confirm, error, hbox, info,
                       label, multiline, run_safely, scroll_wrap)


def _tab(*widgets) -> QWidget:
    host = QWidget()
    lay = QVBoxLayout(host)
    lay.setContentsMargins(0, 12, 8, 12)
    lay.setSpacing(12)
    for w in widgets:
        if isinstance(w, QWidget):
            lay.addWidget(w)
        else:
            lay.addLayout(w)
    lay.addStretch(1)
    return scroll_wrap(host)


class ResetConfirmDialog(QDialog):
    """Last step of a reset: what goes, the owner's password and the word RESET."""

    def __init__(self, parent, pv: dict, lines: list[str]):
        from PySide6.QtWidgets import QDialogButtonBox
        super().__init__(parent)
        self.setWindowTitle("Confirm reset")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 14)
        lay.setSpacing(10)
        head = label("This will permanently remove:", "SectionTitle")
        lay.addWidget(head)
        lay.addWidget(label("<br>".join("• " + x for x in lines) + f"<br><br>Period: <b>{pv['period']}</b>", "", wrap=True))
        lay.addWidget(label("A backup is saved first, so 'Undo last reset' can bring it back.", "Muted", wrap=True))
        form = QFormLayout()
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.Password)
        self.word = QLineEdit()
        self.word.setPlaceholderText("RESET")
        form.addRow("Your password", self.pw)
        form.addRow("Type RESET", self.word)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok = bb.button(QDialogButtonBox.Ok)
        ok.setText("Reset now")
        ok.setProperty("variant", "danger")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.pw.setFocus()


class UserDialog(FormDialog):
    def __init__(self, parent, user: dict | None = None):
        super().__init__(parent, "Edit user" if user else "Add staff account", 420)
        self.user = user
        self.username = QLineEdit(user["username"] if user else "")
        self.username.setEnabled(user is None)
        self.full = QLineEdit(user["full_name"] if user else "")
        self.role = QComboBox()
        for r in ROLES:
            self.role.addItem(ROLE_LABELS[r], r)
        if user:
            self.role.setCurrentIndex(self.role.findData(user["role"]))
        else:
            self.role.setCurrentIndex(self.role.findData("CASHIER"))
        self.active = QCheckBox("Account is active")
        self.active.setChecked(user["is_active"] if user else True)
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.Password)
        self.pw.setPlaceholderText("At least 6 characters" if not user else "Leave blank to keep the current password")
        self.form.addRow("Username", self.username)
        self.form.addRow("Full name", self.full)
        self.form.addRow("Role", self.role)
        self.form.addRow("", self.active)
        self.form.addRow("Password" if not user else "New password", self.pw)
        self.form.addRow(label("Sales staff: billing, quick sale, customers, repairs, expenses – no profit figures.\n"
                               "Technician: repair jobs and spare parts only.\nManager: everything except users, settings "
                               "and reopening closed days.", "Muted", wrap=True))
        self.finish_layout()

    def on_save(self):
        if self.user:
            auth.update_user(self.user["id"], full_name=self.full.text(), role=self.role.currentData(),
                             is_active=self.active.isChecked())
            if self.pw.text():
                auth.reset_password(self.user["id"], self.pw.text())
        else:
            auth.create_user(self.username.text(), self.pw.text(), self.role.currentData(), self.full.text())
        toast("User saved")


class SettingsPage(Page):
    subtitle = "Shop details, invoices, printing, backups and users"

    def __init__(self, parent=None):
        super().__init__(parent)
        u = current_user()
        self.admin = u.can("settings")
        self.tabs = QTabWidget()
        self.tabs.tabBar().setStyleSheet("QTabBar::tab { padding: 8px 9px; margin-right: 2px; }")  # 13 tabs fit
        self.root.addWidget(self.tabs, 1)
        self.tab_index = {}
        if self.admin:
            self._shop_tab()
            self._invoice_tab()
            self._print_tab()
        if u.can("backup"):
            self._backup_tab()
        if self.admin and current_user().role == "ADMIN":
            self._reset_tab()
        if self.admin:
            from ..phone_link import PhoneLinkTab
            self.phone_tab = PhoneLinkTab(self)
            self._add("phone", self.phone_tab, "Phone link")
        if self.admin:
            self._stock_tab()
            self._categories_tab()
            self._users_tab()
        self._account_tab()
        self._appearance_tab()
        if u.can("audit_log"):
            self._audit_tab()
        self._about_tab()

    def _add(self, key, widget, title):
        self.tab_index[key] = self.tabs.addTab(widget, title.replace("&", "&&"))

    # ------------------------------------------------------------ shop
    def _shop_tab(self):
        a = settings.get_all()
        c = Card("Shop details", "shown on invoices, receipts and reports – leave blank what does not apply")
        f = QFormLayout()
        f.setVerticalSpacing(9)
        self.s_name = QLineEdit(a["shop_name"])
        self.s_tag = QLineEdit(a["shop_tagline"])
        self.s_addr = multiline("Shop address", 60)
        self.s_addr.setPlainText(a["shop_address"])
        self.s_phone = QLineEdit(a["shop_phone"])
        self.s_email = QLineEdit(a["shop_email"])
        self.s_gstin = QLineEdit(a["shop_gstin"])
        self.s_upi = QLineEdit(a["upi_id"])
        self.s_upi.setPlaceholderText("e.g. yourshop@okbank – prints a 'scan to pay' QR on bills with a balance")
        self.s_footer = QLineEdit(a["invoice_footer"])
        self.s_support = QLineEdit(a["support_info"])
        self.s_support.setPlaceholderText("e.g. Service & support: call the shop between 10 am and 9 pm")
        self.s_terms = multiline("", 54)
        self.s_terms.setPlainText(a["terms"])
        self.s_sterms = multiline("", 54)
        self.s_sterms.setPlainText(a["service_terms"])
        for t, w in (("Shop name", self.s_name), ("Tagline", self.s_tag), ("Address", self.s_addr), ("Phone", self.s_phone),
                     ("Email", self.s_email), ("GSTIN", self.s_gstin), ("UPI ID", self.s_upi), ("Invoice footer", self.s_footer),
                     ("Customer support", self.s_support), ("Sales terms", self.s_terms), ("Service terms", self.s_sterms)):
            f.addRow(t, w)
        c.add(f)
        lc = Card("Shop logo")
        self.logo_prev = QLabel()
        self._show_logo()
        lc.add(hbox(self.logo_prev, button("Choose logo…", "", "upload", self.choose_logo),
                    button("Use the bundled SDJ logo", "ghost", None, self.reset_logo), None))
        lc.add(label("PNG or JPG. Square images look best. The logo appears on the login screen, sidebar, A4 invoices and reports.",
                     "Muted", wrap=True))
        save = button("Save shop details", "primary", "save", self.save_shop)
        self._add("shop", _tab(c, lc, save), "Shop")

    def _show_logo(self):
        from ..login import logo_pixmap
        self.logo_prev.setPixmap(logo_pixmap(96))

    def choose_logo(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose logo", str(Path.home()), "Images (*.png *.jpg *.jpeg)")
        if not path:
            return
        pm = QPixmap(path)
        if pm.isNull():
            error(self, "That file is not a readable image.")
            return
        dest = config.logo_store_dir() / ("shop_logo" + Path(path).suffix.lower())
        shutil.copy2(path, dest)
        settings.set_value("shop_logo_path", str(dest))
        self._show_logo()
        bus().settings_changed.emit()
        toast("Logo updated")

    def reset_logo(self):
        settings.set_value("shop_logo_path", "")
        self._show_logo()
        bus().settings_changed.emit()

    def save_shop(self):
        settings.set_many(dict(shop_name=self.s_name.text().strip() or "SDJ Mobile Care", shop_tagline=self.s_tag.text().strip(),
                               shop_address=self.s_addr.toPlainText().strip(), shop_phone=self.s_phone.text().strip(),
                               shop_email=self.s_email.text().strip(), shop_gstin=self.s_gstin.text().strip().upper(),
                               upi_id=self.s_upi.text().strip(), invoice_footer=self.s_footer.text().strip(),
                               support_info=self.s_support.text().strip(), terms=self.s_terms.toPlainText().strip(),
                               service_terms=self.s_sterms.toPlainText().strip()))
        self._audit("SETTINGS_SHOP")
        bus().settings_changed.emit()
        toast("Shop details saved")

    def _audit(self, action):
        from ...db.base import session_scope
        with session_scope() as s:
            audit.audit(s, action, "settings", "", {})

    # ------------------------------------------------------------ invoice & tax
    def _invoice_tab(self):
        a = settings.get_all()
        c = Card("Invoice numbering")
        f = QFormLayout()
        self.i_prefix = QLineEdit(a["invoice_prefix"])
        self.i_cn = QLineEdit(a["credit_note_prefix"])
        self.i_job = QLineEdit(a["job_prefix"])
        self.i_pur = QLineEdit(a["purchase_prefix"])
        f.addRow("Invoice prefix", self.i_prefix)
        f.addRow("Credit note prefix", self.i_cn)
        f.addRow("Repair job prefix", self.i_job)
        f.addRow("Purchase prefix", self.i_pur)
        f.addRow(label("Invoice numbers restart each Indian financial year, e.g. SDJ/26-27/00001.", "Muted"))
        c.add(f)
        g = Card("GST & currency", "currency is Indian Rupee (₹)")
        f2 = QFormLayout()
        self.g_on = QCheckBox("Charge GST on bills (shop is GST registered)")
        self.g_on.setChecked(bool(a["gst_enabled"]))
        self.g_incl = QCheckBox("Selling prices already include GST (MRP style)")
        self.g_incl.setChecked(bool(a["prices_include_gst"]))
        self.g_round = QCheckBox("Round bill totals to the nearest rupee")
        self.g_round.setChecked(bool(a["round_off"]))
        self.g_rates = {}
        for k, t in (("default_gst_mobile", "Default GST – mobiles"), ("default_gst_accessory", "Default GST – accessories"),
                     ("default_gst_spare", "Default GST – spare parts"), ("service_gst_rate", "GST on repair labour")):
            cb = QComboBox()
            for r in ("0", "5", "12", "18", "28"):
                cb.addItem(f"{r}%", r)
            cb.setCurrentIndex(max(0, cb.findData(str(int(float(a[k]))))))
            self.g_rates[k] = cb
            f2.addRow(t, cb)
        f2.insertRow(0, "", self.g_on)
        f2.insertRow(1, "", self.g_incl)
        f2.insertRow(2, "", self.g_round)
        g.add(f2)
        g.add(label("When GST is off, bills are plain invoices with no tax and GST paid on purchases is part of product cost.",
                    "Muted", wrap=True))
        save = button("Save invoice & tax settings", "primary", "save", self.save_invoice)
        self._add("invoice", _tab(c, g, save), "Invoice & GST")

    def save_invoice(self):
        vals = dict(invoice_prefix=self.i_prefix.text().strip() or "SDJ", credit_note_prefix=self.i_cn.text().strip() or "CN",
                    job_prefix=self.i_job.text().strip() or "JOB", purchase_prefix=self.i_pur.text().strip() or "PUR",
                    gst_enabled=self.g_on.isChecked(), prices_include_gst=self.g_incl.isChecked(), round_off=self.g_round.isChecked())
        vals.update({k: int(cb.currentData()) for k, cb in self.g_rates.items()})
        settings.set_many(vals)
        self._audit("SETTINGS_TAX")
        toast("Invoice & GST settings saved")

    # ------------------------------------------------------------ printing
    def _print_tab(self):
        a = settings.get_all()
        c = Card("Printing")
        f = QFormLayout()
        self.p_fmt = QComboBox()
        for t, v in (("Thermal receipt 80 mm", "THERMAL_80"), ("Thermal receipt 58 mm", "THERMAL_58"), ("A4 invoice", "A4")):
            self.p_fmt.addItem(t, v)
        self.p_fmt.setCurrentIndex(max(0, self.p_fmt.findData(a["default_print_format"])))
        self.p_after = QComboBox()
        for t, v in (("Show preview with Print button", "ASK"), ("Print immediately to the default printer", "PRINT"),
                     ("Do not print (save PDF only)", "NONE")):
            self.p_after.addItem(t, v)
        self.p_after.setCurrentIndex(max(0, self.p_after.findData(a["print_after_save"])))
        self.p_printer = QComboBox()
        self.p_printer.addItem("Windows default printer", "")
        for p in available_printers():
            self.p_printer.addItem(p, p)
        self.p_printer.setCurrentIndex(max(0, self.p_printer.findData(a["printer_name"])))
        self.p_tlogo = QCheckBox("Print logo on thermal receipts")
        self.p_tlogo.setChecked(bool(a["thermal_logo"]))
        self.p_alogo = QCheckBox("Print logo on A4 invoices")
        self.p_alogo.setChecked(bool(a["a4_logo"]))
        f.addRow("Default bill format", self.p_fmt)
        f.addRow("After saving a bill", self.p_after)
        f.addRow("Printer", self.p_printer)
        f.addRow("", self.p_tlogo)
        f.addRow("", self.p_alogo)
        c.add(f)
        c.add(label("58 mm and 80 mm thermal printers work through their Windows driver – install the driver, then choose it here.",
                    "Muted", wrap=True))
        save = button("Save printing settings", "primary", "save", self.save_print)
        test = button("Print a test receipt", "", "printer", self.test_print)
        self._add("print", _tab(c, hbox(save, test, None)), "Printing")

    def save_print(self):
        settings.set_many(dict(default_print_format=self.p_fmt.currentData(), print_after_save=self.p_after.currentData(),
                               printer_name=self.p_printer.currentData(), thermal_logo=self.p_tlogo.isChecked(),
                               a4_logo=self.p_alogo.isChecked()))
        toast("Printing settings saved")

    def test_print(self):
        from datetime import datetime
        from decimal import Decimal
        from ...output.pdf_docs import invoice_pdf
        sample = dict(invoice_no="TEST-0001", created_at=datetime.now(), business_date=datetime.now().date(),
                      gst_enabled=False, price_includes_tax=True, status="COMPLETED", customer_name="Test print",
                      customer={"name": "Test print", "phone": "", "is_walk_in": True},
                      lines=[dict(product_name="Test item", imei="", quantity=1, unit_price=Decimal("1.00"),
                                  discount=Decimal("0"), taxable=Decimal("1.00"), gst_rate=Decimal("0"), gst=Decimal("0"),
                                  total=Decimal("1.00"), returned_qty=0, warranty_expiry=None)],
                      payments=[], gross_amount=Decimal("1.00"), item_discount=Decimal("0"), bill_discount=Decimal("0"),
                      taxable_amount=Decimal("1.00"), gst_amount=Decimal("0"), round_off=Decimal("0"),
                      grand_total=Decimal("1.00"), balance=Decimal("0"), returned=Decimal("0"), refunded=Decimal("0"))
        path = run_safely(self, invoice_pdf, sample, self.p_fmt.currentData(), config.exports_dir() / "test_receipt.pdf")
        if path:
            show_pdf(path, self, "Test receipt")

    # ------------------------------------------------------------ backup
    def _backup_tab(self):
        a = settings.get_all()
        c = Card("Automatic backup")
        f = QFormLayout()
        self.b_auto = QCheckBox("Back up automatically (daily, after closing the day and when closing the app)")
        self.b_auto.setChecked(bool(a["auto_backup"]))
        self.b_dir = QLineEdit(a["backup_dir"] or str(config.default_backups_dir()))
        self.b_keep = QSpinBox()
        self.b_keep.setRange(5, 365)
        self.b_keep.setValue(int(a["backup_keep"]))
        f.addRow("", self.b_auto)
        f.addRow("Backup folder", hbox(self.b_dir, button("Choose…", "", None, self.choose_backup_dir)))
        f.addRow("Keep last", self.b_keep)
        c.add(f)
        c.add(label("These are plain copies on this PC (for quick undo). The off-PC backups above are the ones that "
                    "survive a dead PC.", "Muted", wrap=True))
        c.add(hbox(button("Save backup settings", "primary", "save", self.save_backup), None))
        bc = Card("Backups")
        self.b_table = DataTable([Col("name", "File", width=360), Col("modified", "Created", "datetime", 170),
                                  Col("size_kb", "Size (KB)", "int", 90)], search=False, stretch="name")
        self.b_table.setMinimumHeight(260)
        bc.add(self.b_table)
        bc.add(hbox(button("Back up now", "primary", "hard-drive-download", self.backup_now),
                    button("Save a copy to…", "", "download", self.backup_to),
                    button("Restore selected", "danger", "archive-restore", self.restore_selected),
                    button("Restore from file…", "danger", "upload", self.restore_file),
                    button("Open folder", "ghost", None, lambda: open_folder(backup.backups_dir())), None))
        from ..offsite_ui import OffsiteCard
        self.offsite_card = OffsiteCard(self)
        self._add("backup", _tab(self.offsite_card, c, bc), "Backup")

    def choose_backup_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Backup folder", self.b_dir.text())
        if d:
            self.b_dir.setText(d)

    def save_backup(self):
        settings.set_many(dict(auto_backup=self.b_auto.isChecked(), backup_dir=self.b_dir.text().strip(),
                               backup_keep=self.b_keep.value()))
        toast("Backup settings saved")
        self.refresh()

    def backup_now(self):
        p = run_safely(self, backup.backup_now, "manual")
        if p:
            toast(f"Backup saved: {Path(p).name}")
            self.refresh()

    def backup_to(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save backup copy", str(Path.home() / "SDJ_backup.db"), "Database (*.db)")
        if path:
            run_safely(self, backup.backup_now, "copy", dest_file=path, success="Backup copy saved")

    def _restore(self, path):
        if not confirm(self, "Restore backup", f"Replace ALL current data with this backup?\n\n{path}\n\n"
                                               "A safety copy of the current data is saved first. The app will close and "
                                               "you will need to open it again.", "Restore", danger=True):
            return
        if run_safely(self, backup.restore_backup, path):
            info(self, "Restored", "The backup was restored. SDJ Mobile Care will now close – please open it again.")
            QApplication.instance().quit()

    def restore_selected(self):
        r = self.b_table.selected()
        if r:
            self._restore(r["path"])

    def restore_file(self):
        from ..offsite_ui import restore_from_file
        if restore_from_file(self, str(backup.backups_dir())):
            info(self, "Restored", "The backup was restored. SDJ Mobile Care will now close – please open it again.")
            QApplication.instance().quit()

    # ------------------------------------------------------------ data reset (owner only)
    RESET_CHOICES = [
        ("sales", "Sales & bills", "quick sales, returns, exchanges and their payments – their stock comes back"),
        ("repairs", "Repair jobs", "with the spare parts used (back to stock) and their payments"),
        ("purchases", "Purchases", "with the supplier payments for them – their stock goes out again"),
        ("expenses", "Expenses", ""),
        ("closings", "Daily closings", "those days become open again"),
        ("stock", "Stock", "every product's stock set to 0 – products stay"),
        ("products", "Products", "delete all items – also clears sales, repairs and purchases"),
        ("customers", "Customers", "only those with no bills or repair jobs left"),
        ("suppliers", "Suppliers", "only those with no purchases left"),
    ]

    def _reset_tab(self):
        c = Card("Reset data", "clear practice data or wrong entries · owner only · a backup is taken first, so it can be undone")
        grid = QVBoxLayout()
        grid.setSpacing(6)
        self.r_boxes = {}
        for key, title, hint in self.RESET_CHOICES:
            cb = QCheckBox(title.replace("&", "&&"))
            cb.toggled.connect(self._reset_rules)
            self.r_boxes[key] = cb
            grid.addLayout(hbox(cb, label(hint, "Muted") if hint else None, None))
        c.add(grid)
        self.r_full = QCheckBox("FULL RESET – everything, like a new install (shop details, logo, settings, logins and "
                                "categories stay)")
        self.r_full.setStyleSheet(f"color: {current().bad}; font-weight: 600;")
        self.r_full.toggled.connect(self._reset_rules)
        c.add(self.r_full)
        self.r_all = QRadioButton("All time")
        self.r_range = QRadioButton("From")
        self.r_all.setChecked(True)
        self.r_from = DateEdit()
        self.r_to = DateEdit()
        for w in (self.r_all, self.r_range):
            w.toggled.connect(self._reset_rules)
        c.add(hbox(QLabel("<b>Period</b>"), self.r_all, self.r_range, self.r_from, QLabel("to"), self.r_to,
                   label("(for sales, repairs, purchases, expenses and closings)", "Muted"), None))
        self.r_preview = label("Tick what to clear and press 'Show what will be removed'.", "Muted", wrap=True)
        self.r_preview.setTextFormat(Qt.RichText)
        c.add(hbox(button("Show what will be removed", "", "eye", self.reset_preview),
                   button("Reset…", "danger", "trash-2", self.reset_run), None))
        c.add(self.r_preview)
        u = Card("Undo last reset", "puts back the backup taken just before the last reset")
        self.r_last = label("", "Muted", wrap=True)
        u.add(self.r_last)
        u.add(hbox(button("Undo last reset", "", "rotate-ccw", self.reset_undo), None))
        u.add(label("Bills, payments or anything else entered after that reset will be lost when you undo it.", "Muted",
                    wrap=True))
        self._add("reset", _tab(c, u), "Reset")
        self._reset_rules()
        self._reset_last()

    def _reset_rules(self):
        """Full reset ticks everything; Products ticks what has to go with it. Unticking gives the old choice back."""
        for _ in range(2):               # twice, so unticking Full also re-evaluates Products
            full = self.r_full.isChecked()
            products = self.r_boxes["products"].isChecked()
            forced = set(reset.MODULES) if full else ({"sales", "repairs", "purchases", "stock"} if products else set())
            before = getattr(self, "_r_forced", set())
            saved = getattr(self, "_r_saved", {})
            for key, cb in self.r_boxes.items():
                cb.blockSignals(True)
                if key in forced and key not in before:
                    saved[key] = cb.isChecked()
                    cb.setChecked(True)
                elif key not in forced and key in before:
                    cb.setChecked(saved.pop(key, False))
                cb.setEnabled(key not in forced or (key == "products" and not full))
                cb.blockSignals(False)
            self._r_forced, self._r_saved = forced, saved
        full = self.r_full.isChecked()
        products = self.r_boxes["products"].isChecked()
        chosen = {k for k, cb in self.r_boxes.items() if cb.isChecked()}
        dated = bool(chosen & reset.DATED) and not full and not products
        for w in (self.r_all, self.r_range):
            w.setEnabled(dated)
        for w in (self.r_from, self.r_to):
            w.setEnabled(dated and self.r_range.isChecked())

    def _reset_selection(self):
        mods = ["all"] if self.r_full.isChecked() else [k for k, cb in self.r_boxes.items() if cb.isChecked()]
        if self.r_range.isChecked() and self.r_range.isEnabled():
            return mods, self.r_from.value(), self.r_to.value()
        return mods, None, None

    def _reset_last(self):
        info_ = reset.last_reset()
        if not info_:
            self.r_last.setText("No reset has been done yet.")
            return
        when = info_.get("at", "").replace("T", " ")[:16]
        what = ", ".join(info_.get("modules") or [])
        self.r_last.setText(f"Last reset: <b>{when}</b> · {what} · {info_.get('period', '')}<br>"
                            + "<br>".join(info_.get("summary") or []))

    def _reset_show(self, pv) -> list[str]:
        p = current()
        if pv["blocked"]:
            self.r_preview.setText(f"<span style='color:{p.bad}'><b>Can't reset this way:</b><br>"
                                   + pv["blocked"].replace("\n", "<br>") + "</span>")
            return []
        lines = reset.summary_lines(pv)
        body = "<br>".join("• " + x for x in lines) or "Nothing to remove."
        notes = "".join(f"<br><i>{n}</i>" for n in pv.get("notes", []))
        self.r_preview.setText(f"<b>Will be removed ({pv['period']}):</b><br>{body}{notes}")
        return lines

    def reset_preview(self):
        mods, a, b = self._reset_selection()
        pv = run_safely(self, reset.preview_reset, mods, a, b)
        if pv:
            self._reset_show(pv)

    def reset_run(self):
        mods, a, b = self._reset_selection()
        pv = run_safely(self, reset.preview_reset, mods, a, b)
        if not pv:
            return
        lines = self._reset_show(pv)
        if pv["blocked"]:
            return
        if not any(pv["counts"].values()):
            info(self, "Reset", "There is nothing to remove for this choice.")
            return
        dlg = ResetConfirmDialog(self, pv, lines)
        if dlg.exec() != QDialog.Accepted:
            return
        st = run_safely(self, reset.run_reset, mods, a, b, password=dlg.pw.text(), confirm=dlg.word.text(), topic="all")
        if not st:
            return
        self._reset_last()
        self.r_preview.setText("Reset done. A backup was saved first – use 'Undo last reset' if you need it back.")
        toast("Reset done")
        info(self, "Reset done", "Removed:\n" + "\n".join("• " + x for x in st["summary"]) +
             (f"\n\n{st['refreshed_closings']} closed day(s) got their figures updated." if st.get("refreshed_closings") else "")
             + "\n\nBackup taken before the reset:\n" + st["backup"])

    def reset_undo(self):
        from PySide6.QtWidgets import QInputDialog
        info_ = reset.last_reset()
        if not info_:
            info(self, "Undo last reset", "There is no reset to undo.")
            return
        when = info_.get("at", "").replace("T", " ")[:16]
        pw, ok = QInputDialog.getText(self, "Undo last reset",
                                      f"Bring back all data as it was before the reset of {when}?\n"
                                      "Anything entered after that reset will be lost. The app closes afterwards.\n\n"
                                      "Enter your password:", QLineEdit.Password)
        if not ok:
            return
        if run_safely(self, reset.undo_last_reset, pw):
            info(self, "Undone", "The data from before the reset is back. SDJ Mobile Care will now close – please open "
                                 "it again.")
            QApplication.instance().quit()

    # ------------------------------------------------------------ stock & service
    def _stock_tab(self):
        a = settings.get_all()
        c = Card("Stock, service and cash defaults")
        f = QFormLayout()
        self.st_low = QSpinBox()
        self.st_low.setRange(0, 9999)
        self.st_low.setValue(int(a["low_stock_default"]))
        self.st_tech = QLineEdit(", ".join(a["technicians"] or []))
        self.st_tech.setPlaceholderText("Comma separated, e.g. Sathish, Imran")
        self.st_warranty = QSpinBox()
        self.st_warranty.setRange(0, 365)
        self.st_warranty.setSuffix(" days")
        self.st_warranty.setValue(int(a["default_repair_warranty_days"]))
        self.st_cash = MoneySpin()
        self.st_cash.set_d(a["default_opening_cash"])
        self.st_exp = QLineEdit(", ".join(a["expense_categories"]))
        f.addRow("Default low-stock level", self.st_low)
        f.addRow("Technicians", self.st_tech)
        f.addRow("Default repair warranty", self.st_warranty)
        f.addRow("Opening cash (first day)", self.st_cash)
        f.addRow("Expense categories", self.st_exp)
        c.add(f)
        save = button("Save", "primary", "save", self.save_stock)
        self._add("stock", _tab(c, save), "Stock & service")

    def save_stock(self):
        cats = [x.strip() for x in self.st_exp.text().split(",") if x.strip()]
        settings.set_many(dict(low_stock_default=self.st_low.value(),
                               technicians=[x.strip() for x in self.st_tech.text().split(",") if x.strip()],
                               default_repair_warranty_days=self.st_warranty.value(),
                               default_opening_cash=str(self.st_cash.value_d()),
                               expense_categories=cats or settings.EXPENSE_CATEGORIES))
        toast("Saved")

    # ------------------------------------------------------------ product categories
    def _categories_tab(self):
        c = Card("Product categories", "pick these when adding a product · the group sets the default GST and the report section")
        muted = lambda r: current().muted if not r["is_active"] else None
        self.c_table = DataTable([Col("name", "Category", width=240, bold=True, color=muted),
                                  Col("group_label", "Group", width=150, color=muted),
                                  Col("imei_label", "IMEI tracking", width=120, color=muted),
                                  Col("products", "Products", "int", 90),
                                  Col("status", "Status", width=90, color=muted)],
                                 placeholder="Search categories…", stretch="name", sortable=False)
        self.c_table.setMinimumHeight(540)
        self.c_table.activated.connect(self.edit_category)
        self.c_hidden = QCheckBox("Show hidden categories")
        self.c_hidden.toggled.connect(self.refresh_categories)
        c.add(self.c_table)
        c.add(hbox(button("Add category", "primary", "plus", self.add_category),
                   button("Edit", "", "pencil", lambda: self.edit_category(self.c_table.selected())),
                   button("Hide / show again", "", "eye", self.toggle_category), None, self.c_hidden))
        c.add(label("Categories are never deleted – old bills and reports keep their category. Hide the ones you "
                    "don't use; they disappear from the Add product list.", "Muted", wrap=True))
        self._add("categories", _tab(c), "Categories")

    def refresh_categories(self):
        rows = inventory.list_categories(active_only=not self.c_hidden.isChecked(), with_counts=True)
        for r in rows:
            r["imei_label"] = "Yes" if r["requires_imei"] else "—"
            r["status"] = "Active" if r["is_active"] else "Hidden"
        self.c_table.set_rows(rows)

    def add_category(self):
        if CategoryDialog(self).exec() == QDialog.Accepted:
            self.refresh_categories()

    def edit_category(self, row):
        if not row:
            info(self, "Categories", "Select a category first.")
            return
        if CategoryDialog(self, row).exec() == QDialog.Accepted:
            self.refresh_categories()

    def toggle_category(self):
        row = self.c_table.selected()
        if not row:
            info(self, "Categories", "Select a category first.")
            return
        hide = row["is_active"]
        if hide and row["products"] and not confirm(
                self, "Hide category", f"Hide '{row['name']}'?\n\nIts {row['products']} product(s) stay exactly as they "
                                       "are – only new products can't pick this category.", yes="Hide"):
            return
        if run_safely(self, inventory.set_category_active, row["id"], not hide, topic="products",
                      success="Category hidden" if hide else "Category visible again"):
            self.refresh_categories()

    # ------------------------------------------------------------ users
    def _users_tab(self):
        c = Card("Staff accounts & roles")
        self.u_table = DataTable([Col("username", "Username", width=130, bold=True), Col("full_name", "Name", width=170),
                                  Col("role_label", "Role", width=170), Col("is_active", "Active", "bool", 70),
                                  Col("last_login_at", "Last login", "datetime", 160)], search=False, stretch="full_name")
        self.u_table.setMinimumHeight(260)
        self.u_table.activated.connect(self.edit_user)
        c.add(self.u_table)
        c.add(hbox(button("Add staff account", "primary", "user-plus", self.add_user),
                   button("Edit / reset password", "", "pencil", lambda: self.edit_user(self.u_table.selected())), None))
        a = Card("Owner approval for staff", "staff can still do protected things – when the owner or a manager types their password")
        self.ap_on = QCheckBox("When staff don't have permission (cancel / edit a bill, adjust stock …), let the owner "
                               "or a manager approve on the spot")
        self.ap_on.setChecked(bool(settings.get("owner_approval")))
        self.ap_disc = QSpinBox()
        self.ap_disc.setRange(0, 100)
        self.ap_disc.setSuffix(" %")
        self.ap_disc.setValue(int(float(settings.get("staff_discount_limit") or 0)))
        self.ap_disc.setFixedWidth(110)
        a.add(self.ap_on)
        a.add(hbox(QLabel("Staff discount limit"), self.ap_disc,
                   label("of the bill · above this the owner / a manager must approve · 0 = no limit", "Muted"), None))
        a.add(hbox(button("Save approval settings", "primary", "save", self.save_approval), None))
        self._add("users", _tab(c, a), "Users")

    def save_approval(self):
        if run_safely(self, settings.set_many, {"owner_approval": self.ap_on.isChecked(),
                                                "staff_discount_limit": self.ap_disc.value()},
                      success="Approval settings saved", topic="settings"):
            bus().settings_changed.emit()

    def add_user(self):
        if UserDialog(self).exec() == QDialog.Accepted:
            self.refresh()

    def edit_user(self, row):
        if row and UserDialog(self, row).exec() == QDialog.Accepted:
            self.refresh()

    # ------------------------------------------------------------ account
    def _account_tab(self):
        u = current_user()
        c = Card("My account", f"{u.display_name} · {ROLE_LABELS.get(u.role, u.role)}")
        f = QFormLayout()
        self.a_old = QLineEdit()
        self.a_old.setEchoMode(QLineEdit.Password)
        self.a_new = QLineEdit()
        self.a_new.setEchoMode(QLineEdit.Password)
        self.a_new2 = QLineEdit()
        self.a_new2.setEchoMode(QLineEdit.Password)
        f.addRow("Current password", self.a_old)
        f.addRow("New password", self.a_new)
        f.addRow("Confirm new password", self.a_new2)
        c.add(f)
        c.add(hbox(button("Change password", "primary", "key", self.change_pw), None))
        self._add("account", _tab(c), "My account")

    def change_pw(self):
        if self.a_new.text() != self.a_new2.text():
            error(self, "The new passwords do not match.")
            return
        if run_safely(self, auth.change_own_password, self.a_old.text(), self.a_new.text(), success="Password changed"):
            for w in (self.a_old, self.a_new, self.a_new2):
                w.clear()

    # ------------------------------------------------------------ appearance
    def _appearance_tab(self):
        c = Card("Theme")
        self.t_light = QRadioButton("Light")
        self.t_dark = QRadioButton("Dark")
        (self.t_dark if (settings.get("theme") == "dark") else self.t_light).setChecked(True)
        c.add(hbox(self.t_light, self.t_dark, None))
        c.add(label("The new theme is applied fully after the next sign-in.", "Muted"))
        c.add(hbox(button("Apply theme", "primary", "sun", self.apply_theme), None))
        self._add("appearance", _tab(c), "Theme")

    def apply_theme(self):
        want = "dark" if self.t_dark.isChecked() else "light"
        from ..theme import current
        if current().name != want:
            self.window().toggle_theme()

    # ------------------------------------------------------------ audit
    def _audit_tab(self):
        c = Card("Audit log", "every important change, with who and when – read-only")
        self.audit_table = DataTable([Col("created_at", "When", "datetime", 160), Col("username", "User", width=100),
                                      Col("action", "Action", width=170, bold=True), Col("entity", "Record", width=110),
                                      Col("entity_id", "ID", width=60), Col("details", "Details", width=420)],
                                     placeholder="Search the audit log…", stretch="details")
        self.audit_table.setMinimumHeight(480)
        c.add(self.audit_table)
        self._add("audit", _tab(c), "Audit log")

    # ------------------------------------------------------------ about
    def _about_tab(self):
        c = Card(APP_TITLE)
        c.add(label(f"Version {APP_VERSION} · offline-first · SQLite database\n\n"
                    f"Database: {config.db_path()}\nDocuments, invoices & reports: {config.documents_root()}\n"
                    f"Logs: {config.logs_dir()}", "", wrap=True))
        c.add(hbox(button("Open documents folder", "", "download", lambda: open_folder(config.documents_root())),
                   button("Open data folder", "ghost", None, lambda: open_folder(config.db_dir())), None))
        c.add(label("Icons: Lucide (ISC licence). Fonts: DejaVu (free licence). Built with Python, PySide6, SQLAlchemy, "
                    "ReportLab, Matplotlib and OpenPyXL.", "Muted", wrap=True))
        self._add("about", _tab(c), "About")

    # ------------------------------------------------------------ refresh
    def refresh(self):
        if hasattr(self, "c_table"):
            self.refresh_categories()
        if hasattr(self, "b_table"):
            self.b_table.set_rows(backup.list_backups())
        if hasattr(self, "offsite_card"):
            self.offsite_card.refresh()
        if hasattr(self, "u_table"):
            self.u_table.set_rows(auth.list_users())
        if hasattr(self, "audit_table"):
            self.audit_table.set_rows(audit.list_audit(1500))

    def open_with(self, payload):
        if isinstance(payload, dict) and payload.get("tab") in self.tab_index:
            self.tabs.setCurrentIndex(self.tab_index[payload["tab"]])
