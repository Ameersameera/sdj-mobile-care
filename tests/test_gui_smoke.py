"""GUI tests (offscreen): every screen opens and the main flows work through the real widgets."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import date
from decimal import Decimal as Dec

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from sdj.core import closing, inventory, repairs, sales, settings


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def gui(db, qapp, monkeypatch):
    msgs = []
    for name in ("critical", "warning", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, _n=name, **k: msgs.append((_n, a)) or QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: msgs.append(("box", self.text())) or 0)
    monkeypatch.setattr(QDialog, "exec", lambda self: 0)
    from sdj.ui.theme import set_theme, stylesheet
    set_theme("light")
    qapp.setStyleSheet(stylesheet())
    from sdj.ui.main_window import MainWindow
    win = MainWindow()
    win.resize(1400, 900)
    win.show()
    qapp.processEvents()
    yield win, msgs, qapp
    win.close()
    win.deleteLater()
    qapp.processEvents()


def _errors(msgs):
    return [m for m in msgs if m[0] in ("critical", "warning")]


def test_every_screen_opens(gui, make_product):
    win, msgs, app = gui
    make_product("Charger", "Chargers", 250, 400, qty=10)
    from sdj.ui.main_window import PAGES
    for key, title, *_ in PAGES:
        win.navigate(key)
        app.processEvents()
        page = win.pages[key]
        page.refresh()
        app.processEvents()
        assert win.title_lbl.text() == title
    assert _errors(msgs) == []
    assert len(win.pages) == 20


def test_quick_sale_screen_saves_and_deducts_stock(gui, make_product):
    win, msgs, app = gui
    pid = make_product("Mobile Charger", "Chargers", 250, 400, qty=50)
    win.navigate("quick_sale")
    page = win.pages["quick_sale"]
    page.set_product(inventory.get_product(pid))
    page.qty.setValue(2)
    page.price.set_d(400)
    page.mode.set("UPI")
    assert page.total.text() == "₹800.00"
    page.save()
    app.processEvents()
    assert _errors(msgs) == []
    assert inventory.get_product(pid)["quantity"] == 48
    s = sales.list_sales()[0]
    assert s["grand_total"] == Dec("800.00") and s["sale_type"] == "QUICK"
    assert sales.get_sale(s["id"])["payments"][0]["mode"] == "UPI"
    # the dashboard reflects it automatically
    win.navigate("dashboard")
    win.pages["dashboard"].refresh()
    assert win.pages["dashboard"].k["sales"].value.text() == "₹800.00"


def test_billing_screen_multi_item_bill(gui, make_product):
    win, msgs, app = gui
    a = make_product("Earphones", "Earphones", 150, 300, qty=20)
    b = make_product("Tempered Glass", "Tempered Glass", 25, 150, qty=30)
    win.navigate("billing")
    page = win.pages["billing"]
    page.add_product(inventory.get_product(a))
    page.add_product(inventory.get_product(a))
    page.add_product(inventory.get_product(b))
    assert len(page.lines) == 2 and page.lines[0]["qty"] == 2
    page.bill_disc.set_d(50)
    assert page.total_lbl.text() == "₹700.00"
    assert page.pay["CASH"].value_d() == Dec("700.00")       # cash pre-filled with the total
    page.print_choice.setCurrentIndex(page.print_choice.findData("NONE"))
    page.save(False)
    app.processEvents()
    assert _errors(msgs) == []
    assert inventory.get_product(a)["quantity"] == 18 and inventory.get_product(b)["quantity"] == 29
    s = sales.get_sale(sales.list_sales()[0]["id"])
    assert s["grand_total"] == Dec("700.00") and len(s["lines"]) == 2
    assert page.lines == []                                     # form reset for the next customer


def test_billing_credit_needs_customer(gui, make_product):
    win, msgs, app = gui
    a = make_product("Speaker", "Bluetooth Speakers", 800, 1500, qty=5)
    win.navigate("billing")
    page = win.pages["billing"]
    page.add_product(inventory.get_product(a))
    page._full("CASH")
    page.pay["CASH"].set_d(500)                                 # part payment, walk-in
    page.print_choice.setCurrentIndex(page.print_choice.findData("NONE"))
    page.save(False)
    assert any("customer" in str(m[1]).lower() for m in _errors(msgs))
    assert sales.list_sales() == [] and inventory.get_product(a)["quantity"] == 5


def test_repair_job_dialog_adds_part(gui, make_product):
    win, msgs, app = gui
    disp = make_product("Display Redmi Note 12", "Displays", 800, 1000, qty=3)
    from sdj.ui.pages.services import JobDialog, NewJobDialog
    win.navigate("services")
    dlg = NewJobDialog(win.pages["services"])
    dlg.phone.setText("9876543210")
    dlg.name.setText("Ravi Kumar")
    dlg.brand.setCurrentText("Xiaomi / Redmi")
    dlg.model.setText("Note 12")
    dlg.problem.setPlainText("Display broken")
    dlg.labour.set_d(500)
    dlg.on_save()
    job_id = dlg.res["id"]
    jd = JobDialog(win.pages["services"], job_id)
    jd._pick_part(inventory.get_product(disp))
    jd.part_price.set_d(1000)
    jd.add_part()
    assert inventory.get_product(disp)["quantity"] == 2
    assert repairs.get_job(job_id)["total"] == Dec("1500.00")
    repairs.deliver_job(job_id, 1500, "CASH")
    jd.load()
    assert jd.j["status"] == "DELIVERED" and _errors(msgs) == []


def test_daily_closing_screen(gui, make_product, monkeypatch):
    win, msgs, app = gui
    pid = make_product("Charger", "Chargers", 250, 400, qty=10)
    sales.quick_sale(pid, 1, 400, "CASH")
    import sdj.ui.pages.daily_closing as dc
    monkeypatch.setattr(dc, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(dc, "show_pdf", lambda *a, **k: None)
    win.navigate("closing")
    page = win.pages["closing"]
    page.refresh()
    assert page._expected == Dec("400.00")
    page.actual.set_d(400)
    page.keep.set_d(100)
    page.close_day()
    rec = closing.list_closings()[0]
    assert rec["business_date"] == date.today() and rec["difference"] == 0 and rec["carry_forward"] == Dec("100.00")
    from datetime import timedelta
    assert closing.suggested_opening_cash(date.today() + timedelta(days=1)) == Dec("100.00")


def test_settings_screen_saves_shop_details(gui):
    win, msgs, app = gui
    win.navigate("settings")
    page = win.pages["settings"]
    page.s_phone.setText("")
    page.s_addr.setPlainText("Main Road")
    page.save_shop()
    assert settings.get("shop_address") == "Main Road" and settings.get("shop_phone") == ""


def test_expenses_screen_add_edit_void(gui, monkeypatch):
    win, msgs, app = gui
    win.navigate("expenses")
    page = win.pages["expenses"]
    page.category.setCurrentText("Electricity")
    page.amount.set_d(1850)
    page.desc.setText("EB bill")
    page.mode.set("UPI")
    page.save()
    from sdj.core import expenses
    rows = expenses.list_expenses()
    assert len(rows) == 1 and rows[0]["amount"] == Dec("1850.00") and rows[0]["payment_mode"] == "UPI"
    page.edit(rows[0])
    page.amount.set_d(1900)
    page.save()
    assert expenses.list_expenses()[0]["amount"] == Dec("1900.00")
    import sdj.ui.pages.expenses as ex
    monkeypatch.setattr(ex, "ask_text", lambda *a, **k: "duplicate entry")
    page.refresh()
    page.table.view.selectRow(0)
    page.void()
    assert expenses.list_expenses() == [] and _errors(msgs) == []


def test_purchase_screen_adds_stock(gui, make_product):
    win, msgs, app = gui
    from sdj.core import parties
    pid = make_product("Charger 33W", "Chargers", 380, 650, qty=2)
    sup = parties.create_supplier(dict(name="Test Wholesale"))
    win.navigate("purchases")
    page = win.pages["purchases"]
    page.refresh()
    page.supplier.setCurrentIndex(page.supplier.findData(sup))
    page.add_line(inventory.get_product(pid))
    page.lines[0]["qty"] = 10
    page.lines[0]["price"] = Dec("380")
    page.render()
    page.paid.set_d(1000)
    page.save()
    assert _errors(msgs) == []
    assert inventory.get_product(pid)["quantity"] == 12
    assert parties.list_suppliers()[0]["due"] > 0


def test_returns_screen_refund(gui, make_product, monkeypatch):
    win, msgs, app = gui
    pid = make_product("Power Bank", "Power Banks", 600, 1000, qty=5)
    s = sales.create_sale([sales.CartItem(pid, 2, Dec("1000"))], payments={"CASH": Dec("2000")})
    import sdj.ui.pages.returns as rp
    monkeypatch.setattr(rp, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(rp, "show_pdf", lambda *a, **k: None)
    win.navigate("returns")
    page = win.pages["returns"]
    page.inv.setText(s["invoice_no"])
    page.find()
    ln, spin, cond = page.spins[0]
    spin.setValue(1)
    assert page.refund_amt.value_d() == Dec("1000.00")
    page.process()
    assert _errors(msgs) == []
    assert inventory.get_product(pid)["quantity"] == 4
    assert sales.get_sale(s["id"])["returned"] == Dec("1000.00")


def test_customer_dues_collection_screen(gui, make_product):
    win, msgs, app = gui
    from sdj.core import parties
    pid = make_product("Smartwatch", "Smartwatches", 1200, 2000, qty=2)
    cid = parties.create_customer(dict(name="Meena", phone="9000000001"))
    sales.create_sale([sales.CartItem(pid, 1, Dec("2000"))], customer_id=cid, payments={"CASH": Dec("500")})
    win.navigate("customers")
    page = win.pages["customers"]
    page.refresh()
    assert page.k_dues.value.text() == "₹1,500.00"
    page.dues_only.setChecked(True)
    assert len(page.table.visible_rows()) == 1


def test_settings_categories_tab_add_hide_show(gui, monkeypatch):
    win, msgs, app = gui
    win.navigate("settings")
    page = win.pages["settings"]
    page.open_with({"tab": "categories"})
    page.refresh()
    app.processEvents()
    assert page.tabs.currentIndex() == page.tab_index["categories"]
    from sdj.ui.dialogs import CategoryDialog
    dlg = CategoryDialog(page, group="SPARE")
    dlg.name.setText("Back Panels")
    dlg._accept()
    assert dlg.result_id and dlg.result() == QDialog.Accepted
    page.refresh_categories()
    rows = {r["name"]: r for r in page.c_table.visible_rows()}
    assert rows["Back Panels"]["group"] == "SPARE" and rows["Back Panels"]["status"] == "Active"
    monkeypatch.setattr(page.c_table, "selected", lambda: rows["Back Panels"])
    page.toggle_category()
    assert "Back Panels" not in {r["name"] for r in page.c_table.visible_rows()}
    page.c_hidden.setChecked(True)
    hidden = {r["name"]: r for r in page.c_table.visible_rows()}
    assert hidden["Back Panels"]["status"] == "Hidden"
    monkeypatch.setattr(page.c_table, "selected", lambda: hidden["Back Panels"])
    page.toggle_category()
    assert inventory.get_category(dlg.result_id)["is_active"] is True
    assert _errors(msgs) == []


def test_product_dialog_plus_button_adds_category(gui):
    win, msgs, app = gui
    from sdj.ui.dialogs import CategoryDialog, ProductDialog
    pd = ProductDialog(win, group="SPARE")
    assert not pd.cat_add.isHidden()
    assert pd.category.currentData()["group"] == "SPARE"
    cd = CategoryDialog(pd, group=pd.category.currentData()["group"])
    cd.name.setText("Back Glass")
    cd._accept()
    pd.category_added(cd.result_id)
    assert pd.category.currentData()["name"] == "Back Glass"
    pd.name.setText("Back glass – Redmi 13C")
    pd.purchase.set_d(150)
    pd.selling.set_d(350)
    pd._accept()
    assert _errors(msgs) == []
    p = [x for x in inventory.list_products() if x["name"] == "Back glass – Redmi 13C"][0]
    assert p["category_id"] == cd.result_id


def test_reset_tab_rules_preview_and_run(gui, make_product, monkeypatch):
    win, msgs, app = gui
    c = make_product("Charger", "Chargers", 250, 400, qty=10)
    sales.quick_sale(c, 2, 400, "CASH")
    from sdj.ui.pages import settings_page
    win.navigate("settings")
    page = win.pages["settings"]
    page.open_with({"tab": "reset"})
    app.processEvents()
    assert page.tabs.currentIndex() == page.tab_index["reset"]
    page.r_boxes["sales"].setChecked(True)
    page.reset_preview()
    assert "1 bill(s)" in page.r_preview.text()
    page.r_boxes["products"].setChecked(True)            # products drags its history along …
    assert page.r_boxes["stock"].isChecked() and not page.r_boxes["stock"].isEnabled() and not page.r_all.isEnabled()
    page.r_boxes["products"].setChecked(False)           # … and gives the old choice back
    assert page.r_boxes["sales"].isChecked() and not page.r_boxes["stock"].isChecked() and page.r_all.isEnabled()
    page.r_full.setChecked(True)
    assert all(cb.isChecked() for cb in page.r_boxes.values())
    page.r_full.setChecked(False)
    assert [k for k, cb in page.r_boxes.items() if cb.isChecked()] == ["sales"]

    def fake_exec(self):
        self.pw.setText("admin123")
        self.word.setText("RESET")
        return QDialog.Accepted
    monkeypatch.setattr(settings_page.ResetConfirmDialog, "exec", fake_exec)
    page.reset_run()
    assert _errors(msgs) == []
    assert inventory.get_product(c)["quantity"] == 10 and sales.list_sales() == []
    assert "Last reset" in page.r_last.text()


def test_billing_edit_mode_keeps_the_invoice_number(gui, make_product):
    win, msgs, app = gui
    from sdj.core.sales import CartItem
    c = make_product("Charger", "Chargers", 250, 400, qty=10)
    cab = make_product("Cable", "USB Cables", 60, 150, qty=10)
    s1 = sales.create_sale([CartItem(c, 2, Dec("400"))], payments={"CASH": Dec("800")})
    from sdj.ui.dialogs import SaleDetailDialog
    assert not SaleDetailDialog(win, s1["id"]).edit_btn.isHidden()
    win.navigate("billing", {"edit_sale_id": s1["id"]})
    app.processEvents()
    page = win.pages["billing"]
    assert page._edit and not page.edit_bar.isHidden() and page.lines[0]["qty"] == 2
    assert page.pay["CASH"].value_d() == Dec("800")
    page.lines[0]["qty"] = 1
    page.add_product(inventory.get_product(cab))
    page._full("UPI")
    page.print_choice.setCurrentIndex(page.print_choice.findData("NONE"))
    page.save(False)
    assert _errors(msgs) == []
    rows = {r["invoice_no"]: r for r in sales.list_sales()}
    assert rows[s1["invoice_no"]]["grand_total"] == Dec("550.00") and rows[s1["invoice_no"] + "-E1"]["status"] == "CANCELLED"
    assert page._edit is None and page.edit_bar.isHidden()
    assert inventory.get_product(c)["quantity"] == 9


def test_staff_gets_owner_approval_to_cancel(gui, make_product, monkeypatch):
    win, msgs, app = gui
    from sdj.core import auth
    from sdj.core.sales import CartItem
    from sdj.ui import widgets
    from sdj.ui.dialogs import SaleDetailDialog
    c = make_product("Charger", "Chargers", 250, 400, qty=10)
    s1 = sales.create_sale([CartItem(c, 1, Dec("400"))], payments={"CASH": Dec("400")})
    auth.create_user("staff", "staff123", "CASHIER", "Counter staff")
    auth.set_current_user(auth.authenticate("staff", "staff123"))
    dlg = SaleDetailDialog(win, s1["id"])
    assert not dlg.cancel_btn.isHidden()                 # visible because the owner can approve
    monkeypatch.setattr(widgets, "ask_text", lambda *a, **k: "Wrong item")

    def approve(self):
        self.approver = auth.verify_approver("admin", "admin123", self.perm)
        return QDialog.Accepted
    monkeypatch.setattr(widgets.ApprovalDialog, "exec", approve)
    dlg._cancel()
    assert _errors(msgs) == []
    assert sales.get_sale(s1["id"])["status"] == "CANCELLED"
    assert not auth.current_user().can("cancel_sale")
    settings.set_value("owner_approval", False)
    s2 = sales.create_sale([CartItem(c, 1, Dec("400"))], payments={"CASH": Dec("400")})
    assert SaleDetailDialog(win, s2["id"]).cancel_btn.isHidden()


def test_phone_link_tab_code_qr_devices_and_alerts(gui, make_product, monkeypatch):
    win, msgs, app = gui
    from sdj.core import phonesync, syncserver
    from sdj.ui.phone_link import SyncAlertsDialog, start_bridge
    monkeypatch.setattr(phonesync, "PORT", 0)
    start_bridge()
    win.navigate("settings")
    page = win.pages["settings"]
    page.tabs.setCurrentIndex(page.tab_index["phone"])
    tab = page.phone_tab
    tab.unlock_pw.setText("wrong")
    tab.unlock()
    assert tab.content is None                               # password needed
    assert msgs and "password" in msgs[-1][1][2]
    msgs.clear()
    tab.unlock_pw.setText("admin123")
    tab.unlock()
    app.processEvents()
    assert tab.stack.currentWidget() is tab.content
    monkeypatch.setattr(syncserver, "_service", syncserver.SyncServer(port=0, udp_port=0, host="127.0.0.1"))
    tab.create_code()
    app.processEvents()
    assert tab.code_box.isVisible() and len(tab.code_lbl.text()) == 11 and tab.qr.matrix
    assert syncserver.service().running
    # a phone links and sends a bill that conflicts with nothing but lands on a closed day → alert
    from test_phone_sync import FakePhone
    ch = make_product("Charger", "Chargers", 250, 400, qty=10)
    phone = FakePhone()
    phone.pair(tab.code_lbl.text())
    phone.sync(full=True)
    closing.close_day(date.today(), 0, make_pdf=False, make_backup=False)
    phone.sync([phone.bill([dict(p=ch, q=1, price=40000)], {"CASH": 40000})])
    app.processEvents()
    tab.refresh()
    assert tab.dev_table.rows()[0]["series"] == "P1"
    assert "1 open alert" in tab.alert_lbl.text()
    dlg = SyncAlertsDialog(win)
    assert dlg.table.rows()[0]["kind"] == "CLOSED_DAY"
    win.update_header()
    assert not win.phone_pill.isHidden()
    syncserver.service().stop()
    assert _errors(msgs) == []


def test_phone_link_tab_turns_backups_on_for_a_staff_phone(gui, monkeypatch):
    win, msgs, app = gui
    from sdj.core import auth, phonesync, syncserver
    from sdj.ui.phone_link import start_bridge
    from test_phone_sync import FakePhone
    start_bridge()
    monkeypatch.setattr(syncserver, "_service", syncserver.SyncServer(port=0, udp_port=0, host="127.0.0.1"))
    uid = auth.create_user("kavin", "staff123", "CASHIER", "Kavin")
    FakePhone().pair(phonesync.new_link_code(uid)["code"], name="Staff phone")
    win.navigate("settings")
    page = win.pages["settings"]
    page.tabs.setCurrentIndex(page.tab_index["phone"])
    tab = page.phone_tab
    tab.unlock_pw.setText("admin123")
    tab.unlock()
    app.processEvents()
    assert tab.dev_table.rows()[0]["backups_label"] == "No"          # staff phones: off unless turned on
    tab.dev_table.view.selectRow(0)
    tab.toggle_backups()
    assert tab.dev_table.rows()[0]["backups_label"] == "Yes" and phonesync.list_devices()[0]["keeps_backups"]
    syncserver.service().stop()
    assert _errors(msgs) == []


def test_settings_backup_tab_off_pc_backups_and_recovery_code(gui, tmp_path, monkeypatch):
    win, msgs, app = gui
    from datetime import datetime, timedelta
    from PySide6.QtWidgets import QFileDialog
    from sdj.ui import offsite_ui, phone_link
    win.navigate("settings")
    page = win.pages["settings"]
    page.tabs.setCurrentIndex(page.tab_index["backup"])
    card = page.offsite_card
    assert "No off-PC backup yet" in card.status_lbl.text()
    card.backup_now()
    assert "Last off-PC backup" in card.status_lbl.text() and "checked" in card.status_lbl.text()
    pen = tmp_path / "pendrive"
    pen.mkdir()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(pen)))
    card.choose_folder()
    assert list(pen.glob("*.sdjb")) and f"2nd folder: {pen}" in card.status_lbl.text()

    def pw_exec(self):
        self.pw.setText("admin123")
        return QDialog.Accepted
    monkeypatch.setattr(phone_link.PasswordDialog, "exec", pw_exec)
    shown = []
    monkeypatch.setattr(offsite_ui.RecoveryCodeDialog, "exec", lambda self: shown.append(self.code) or 1)
    card.make_code()
    assert shown and len(shown[0]) == 29 and "Recovery code: made" in card.status_lbl.text()
    dlg = offsite_ui.RecoveryCodeDialog(win, shown[0])
    assert not dlg.ok_btn.isEnabled()
    dlg.done_box.setChecked(True)
    assert dlg.ok_btn.isEnabled()
    from sdj.output.pdf_recovery import recovery_code_pdf
    assert recovery_code_pdf(shown[0], settings.shop_info(), tmp_path / "rc.pdf").read_bytes().startswith(b"%PDF")
    win.update_header()
    assert win.backup_pill.isHidden()
    settings.set_value("offsite_second_last", (datetime.now() - timedelta(days=9)).isoformat())
    win.update_header()
    assert not win.backup_pill.isHidden() and "2nd backup folder" in win.backup_pill.toolTip()
    win.navigate("dashboard")
    win.pages["dashboard"].refresh()
    assert "2nd folder" in win.pages["dashboard"].backup_btn.text()
    assert _errors(msgs) == []


def test_new_pc_first_screen_restores_from_phone_or_file(db, qapp, make_product, tmp_path, monkeypatch):
    """A fresh install offers: restore from phone (QR → backup arrives → password), restore from a file, start fresh."""
    import hashlib
    import json
    import urllib.request
    from pathlib import Path
    from PySide6.QtWidgets import QFileDialog
    from sdj.core import auth, offsite, phonesync, syncserver
    from sdj.core.phonesync import pair_key, seal
    from sdj.ui import offsite_ui
    from sdj.ui.login import SetupWizard
    from sdj.ui.theme import set_theme, stylesheet
    from test_offsite import _new_pc
    msgs = []
    for name in ("critical", "warning", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, _n=name, **k: msgs.append((_n, a)) or QMessageBox.Ok))
    make_product("Charger 20W", "Chargers", 250, 400, qty=10)
    bundle = Path(offsite.create_bundle("manual")["path"])
    blob = bundle.read_bytes()
    keep = tmp_path / "pendrive" / bundle.name
    keep.parent.mkdir()
    keep.write_bytes(blob)
    _new_pc(monkeypatch, tmp_path, "pc2")
    set_theme("light")
    qapp.setStyleSheet(stylesheet())
    wiz = SetupWizard()
    wiz.show()
    qapp.processEvents()
    assert wiz.stack.currentIndex() == 0
    monkeypatch.setattr(syncserver, "_service", syncserver.SyncServer(port=0, udp_port=0, host="127.0.0.1"))
    dlg = offsite_ui.RestoreFromPhoneDialog(wiz)
    dlg.show()
    qapp.processEvents()
    code = dlg.code_lbl.text().replace("-", "")
    assert len(code) == 10 and dlg.qr.matrix and syncserver.service().running
    pc = phonesync.pc_identity()
    meta = dict(name=bundle.name, size=len(blob), sha=hashlib.sha256(blob).hexdigest(), phone="Owner phone")
    env = seal(pair_key(code, pc), meta, b"restore")
    env["pc"] = pc
    req = urllib.request.Request(f"http://127.0.0.1:{syncserver.service().port}/sdj/restore", data=blob,
                                 headers={"X-SDJ-Auth": json.dumps(env), "Content-Type": "application/octet-stream"})
    assert json.loads(urllib.request.urlopen(req, timeout=30).read())
    for _ in range(20):
        qapp.processEvents()
    assert dlg.pages.currentIndex() == 1 and "Owner phone" in dlg.file_lbl.text() and "1 products" in dlg.file_lbl.text()
    dlg.secret.setText("wrong")
    dlg.restore_file()
    assert not dlg.restored and msgs and msgs[-1][0] == "critical"
    dlg.secret.setText("admin123")
    dlg.restore_file()
    assert dlg.restored and auth.user_count() == 1
    assert not syncserver.service().running and phonesync.restore_code_info() is None   # closed with the dialog
    # another new PC: restore from a file (pen drive / Google Drive download)
    _new_pc(monkeypatch, tmp_path, "pc3")
    wiz2 = SetupWizard()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(keep), "")))

    def secret_exec(self):
        self.secret.setText("admin123")
        return QDialog.Accepted
    monkeypatch.setattr(offsite_ui.SecretDialog, "exec", secret_exec)
    wiz2.restore_file()
    assert wiz2.restored and wiz2.result() == QDialog.Accepted and auth.user_count() == 1
    assert len([m for m in msgs if m[0] == "critical"]) == 1          # only the wrong password above
    wiz3 = SetupWizard()
    wiz3.stack.setCurrentIndex(1)
    assert wiz3.username.text() == "admin"


def test_update_popup_and_settings_check(gui, tmp_path, monkeypatch):
    """Settings → About has Check for updates; a new release shows the popup with what's new; nothing installs in tests."""
    win, msgs, app = gui
    from sdj.core import updater
    from sdj.ui import update_ui
    from sdj.ui.pages.settings_page import SettingsPage

    latest = dict(version="9.9.9", tag="v9.9.9", notes="Update from inside the app", page="https://github.com/x/y",
                  setup=dict(name="SDJMobileCare_Setup_9.9.9.exe", url="https://github.com/x", size=1, sha="a" * 64), apk=None)
    page = SettingsPage()
    page.tabs.setCurrentIndex(page.tab_index["about"])
    assert page.update_auto.isChecked()
    page.update_auto.setChecked(False)
    assert settings.get("update_auto_check") is False
    assert "Last checked" in page.update_status.text()

    # popup content
    dlg = update_ui.UpdateDialog(latest)
    from PySide6.QtWidgets import QTextBrowser
    assert "inside the app" in dlg.findChild(QTextBrowser).toPlainText()
    assert not updater.can_self_install()
    assert dlg.go_btn.text() == "Open download page"

    # manual check: up to date / offline / new
    ctl = update_ui.UpdateController(win)
    ctl._on_checked(dict(state="current", latest=latest), True)
    ctl._on_checked(dict(state="offline", latest=None), True)
    assert [m for m in msgs if m[0] == "information"].__len__() == 2
    shown = []
    monkeypatch.setattr(update_ui.UpdateDialog, "exec", lambda self: shown.append(self.latest["version"]) or 0)
    ctl._on_checked(dict(state="new", latest=latest), False)
    assert shown == ["9.9.9"]
    settings.set_value("update_skip_version", "9.9.9")                # "Skip this version": no quiet popup again…
    ctl._on_checked(dict(state="new", latest=latest), False)
    assert shown == ["9.9.9"]
    ctl._on_checked(dict(state="new", latest=latest), True)           # …but Check for updates still shows it
    assert shown == ["9.9.9", "9.9.9"]


def test_whats_new_card_history_and_later(gui, monkeypatch):
    win, msgs, app = gui
    from PySide6.QtWidgets import QTextBrowser
    from sdj.core import updater
    from sdj.ui import update_ui
    entry = dict(version="9.9.9", notes="- Popup on the phone", date="2026-11-25")
    dlg = update_ui.WhatsNewDialog(entry)
    assert "9.9.9" in dlg.windowTitle() or dlg.findChild(QTextBrowser).toPlainText().startswith("- Popup")
    settings.set_value("update_history", [entry])
    hist = update_ui.HistoryDialog()
    assert "Version 9.9.9" in hist.findChild(QTextBrowser).toPlainText()
    latest = dict(version="9.9.9", tag="v9.9.9", notes="- Popup", page="https://github.com/x/y", date="2026-11-25",
                  recommended=True, setup=dict(name="SDJMobileCare_Setup_9.9.9.exe", url="https://github.com/x", size=62_000_000, sha="a" * 64),
                  apk=None)
    pop = update_ui.UpdateDialog(latest)
    labels = " ".join(w.text() for w in pop.findChildren(__import__("PySide6.QtWidgets", fromlist=["QLabel"]).QLabel))
    assert "59 MB" in labels and "25 Nov 2026" in labels and "Recommended" in labels
    pop._later()
    assert updater.snoozed()
    shown = []
    monkeypatch.setattr(update_ui.UpdateDialog, "exec", lambda self: shown.append(1) or 0)
    ctl = update_ui.UpdateController(win)
    ctl._on_checked(dict(state="new", latest=latest), False)          # snoozed: no quiet popup
    assert shown == []
    ctl._on_checked(dict(state="new", latest=latest), True)           # but Check for updates still shows it
    assert shown == [1]
    for w in (dlg, hist, pop):                                        # parent-less widgets: delete them before Qt shuts down
        w.deleteLater()
    app.processEvents()


def test_update_found_while_in_the_tray_gives_a_windows_notice_then_the_popup(gui, monkeypatch):
    """v1.5.2: no window open (program in the tray) → one Windows notification per version; the popup comes once the
    owner opens the window."""
    win, msgs, app = gui
    from PySide6.QtTest import QTest
    from sdj.ui import update_ui
    notices = []

    class FakeTray:
        def notify(self, title, text, on_click=None, msecs=10000):
            notices.append(title)

    latest = dict(version="9.9.9", tag="v9.9.9", notes="- Faster sync", page="https://github.com/x/y", date="2026-11-25",
                  recommended=False, setup=dict(name="SDJMobileCare_Setup_9.9.9.exe", url="https://github.com/x", size=1,
                                                sha="a" * 64), apk=None)
    ctl = update_ui.UpdateController(None, FakeTray())
    ctl._on_checked(dict(state="new", latest=latest), False)
    ctl._on_checked(dict(state="new", latest=latest), False)
    assert notices == ["SDJ Mobile Care 9.9.9 is ready"]
    shown = []
    monkeypatch.setattr(update_ui.UpdateDialog, "exec", lambda self: shown.append(self.latest["version"]) or 0)
    ctl._whats_new_done = True
    ctl.set_window(win)
    QTest.qWait(3400)
    assert shown == ["9.9.9"]
    ctl.set_window(None)
    ctl.deleteLater()


def test_window_closes_to_the_tray_only_in_background_mode(gui, monkeypatch):
    win, msgs, app = gui
    from sdj.ui import tray
    monkeypatch.setattr(tray.life, "quitting", False)
    went = []
    win.close_to_tray = lambda: went.append(1) or True
    assert win.close() is False and went == [1]            # stays running (the program keeps it in the tray)
    monkeypatch.setattr(tray.life, "quitting", True)        # Quit / update / Windows shutting down: really closes
    win.close_to_tray = lambda: went.append(2) or True
    assert win.close() is True and went == [1]
    assert isinstance(tray.available(), bool)
