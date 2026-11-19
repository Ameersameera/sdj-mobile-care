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
