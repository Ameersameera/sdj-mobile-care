"""Data reset: per module, by date range, full reset, safety checks, stock rebuild and undo."""
import sqlite3
from datetime import date, timedelta
from decimal import Decimal as Dec

import pytest

from sdj import config
from sdj.core import auth, closing, expenses, inventory, parties, purchases, repairs, reset, returns, sales
from sdj.core.errors import BusinessError, PermissionDenied, ValidationError
from sdj.core.sales import CartItem
from tests.conftest import make_imei


def _db():
    return sqlite3.connect(str(config.db_path()))


def _count(table, where="1=1"):
    con = _db()
    try:
        return con.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]
    finally:
        con.close()


def _consistent():
    """Stock always equals the movement history, IMEI units match stock, no broken links, triggers in place."""
    con = _db()
    try:
        for pid, qty, track in con.execute("SELECT id, quantity, track_imei FROM products"):
            total = con.execute("SELECT COALESCE(SUM(qty_change), 0) FROM stock_movements WHERE product_id=?", (pid,)).fetchone()[0]
            last = con.execute("SELECT qty_after FROM stock_movements WHERE product_id=? ORDER BY id DESC LIMIT 1", (pid,)).fetchone()
            assert total == qty and (last is None or last[0] == qty), (pid, qty, total, last)
            if track:
                assert con.execute("SELECT COUNT(*) FROM imei_records WHERE product_id=? AND status='IN_STOCK'",
                                   (pid,)).fetchone()[0] == qty
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        assert con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger'").fetchone()[0] == 17
    finally:
        con.close()


@pytest.fixture
def shop(db, make_product):
    """Day 1 (closed) and day 2 (open) with sales, a return, a purchase, a repair and expenses."""
    today = date.today()
    sup = parties.create_supplier(dict(name="Test Distributor", phone="9000000001"))
    cust = parties.create_customer(dict(name="Ravi Kumar", phone="9000000002"))
    charger = make_product("Charger 20W", "Chargers", 250, 400, qty=50)
    phone = make_product("Redmi 13C", "Smartphones", 9000, 11000, qty=3, imeis=[make_imei(i) for i in (1, 2, 3)])
    display = make_product("Display A15", "Displays", 800, 1200, qty=10)
    # ---- day 1
    purchases.create_purchase(sup, [dict(product_id=charger, quantity=20, unit_price=Dec("240"), gst_rate=0)],
                              paid_amount=4800, payment_mode="CASH")
    q1 = sales.quick_sale(charger, 2, 400, "CASH")
    ph = inventory.available_imeis(phone)[0]
    b1 = sales.create_sale([CartItem(phone, 1, Dec("11000"), imei_record_id=ph["id"]),
                            CartItem(charger, 1, Dec("400"))], customer_id=cust, payments={"UPI": Dec("11400")})
    expenses.add_expense("Food", 150, payment_mode="CASH")
    job1 = repairs.create_job(dict(customer_name="Ravi Kumar", customer_phone="9000000002", brand="Samsung",
                                   model="A15", problem="Display", labour_charge=500))
    repairs.add_part(job1["id"], display, 1, 1200)
    closing.close_day(today, actual_cash=Dec("0"), opening_cash=0, make_pdf=False, make_backup=False)
    day2 = today + timedelta(days=1)
    # ---- day 2 (business day moved on because day 1 is closed)
    q2 = sales.quick_sale(charger, 3, 400, "CASH")
    ret = returns.create_return(b1["id"], [dict(sale_item_id=sales.get_sale(b1["id"])["lines"][1]["id"], quantity=1)],
                                reason="Not working", refund_mode="CASH")
    expenses.add_expense("Electricity", 1850, payment_mode="UPI")
    return dict(today=today, day2=day2, charger=charger, phone=phone, display=display, cust=cust, sup=sup,
                q1=q1, b1=b1, q2=q2, ret=ret, job1=job1, phone_unit=ph["id"])


def test_preview_matches_reset_and_needs_owner_password(shop):
    pv = reset.preview_reset(["sales", "expenses"], shop["day2"], shop["day2"])
    assert pv["counts"]["sales"] == 1 and pv["counts"]["returns"] == 1 and pv["counts"]["expenses"] == 1
    assert not pv["blocked"]
    assert _count("sales") == 3                          # preview changed nothing
    with pytest.raises(ValidationError):
        reset.run_reset(["sales"], shop["day2"], shop["day2"], password="admin123", confirm="reset please")
    with pytest.raises(ValidationError):
        reset.run_reset(["sales"], shop["day2"], shop["day2"], password="wrong", confirm="RESET")
    st = reset.run_reset(["sales", "expenses"], shop["day2"], shop["day2"], password="admin123", confirm="RESET")
    assert st["counts"] == pv["counts"] and st["amounts"] == pv["amounts"]
    assert _count("sales") == 2 and _count("returns") == 0 and _count("expenses") == 1
    _consistent()


def test_sales_reset_for_a_date_range_restores_stock_and_bills(shop):
    c = shop["charger"]
    assert inventory.get_product(c)["quantity"] == 50 + 20 - 2 - 1 - 3 + 1
    reset.run_reset(["sales"], shop["day2"], shop["day2"], password="admin123", confirm="RESET")
    # day-2 quick sale gone (3 back) and the day-2 return of bill 1 gone (1 out again)
    assert inventory.get_product(c)["quantity"] == 50 + 20 - 2 - 1
    b1 = sales.get_sale(shop["b1"]["id"])
    assert b1["returned"] == Dec("0.00") and b1["refunded"] == Dec("0.00") and b1["lines"][1]["returned_qty"] == 0
    assert b1["balance"] == Dec("0.00")
    _consistent()


def test_reset_all_sales_frees_phones_and_restarts_invoice_numbers(shop):
    reset.run_reset(["sales"], password="admin123", confirm="RESET")
    assert _count("sales") == 0 and _count("payments", "ref_type='SALE'") == 0
    assert inventory.get_product(shop["phone"])["quantity"] == 3
    assert {u["id"] for u in inventory.available_imeis(shop["phone"])} >= {shop["phone_unit"]}
    new = sales.quick_sale(shop["charger"], 1, 400, "CASH")
    assert new["invoice_no"].endswith("/00001")
    _consistent()


def test_purchase_reset_is_blocked_when_its_stock_was_sold(shop, make_product):
    # all 20 purchased chargers are still covered by opening stock, so removing the purchase is fine …
    pv = reset.preview_reset(["purchases"], shop["today"], shop["today"])
    assert pv["counts"]["purchases"] == 1 and not pv["blocked"]
    # … but a phone bought on a purchase and then sold can't lose its purchase alone
    sup = shop["sup"]
    im = make_imei(77)
    purchases.create_purchase(sup, [dict(product_id=shop["phone"], quantity=1, unit_price=Dec("9000"), gst_rate=0,
                                         imei_units=[{"imei1": im}])], paid_amount=0)
    unit = [u for u in inventory.available_imeis(shop["phone"]) if u["imei1"] == im][0]
    sales.create_sale([CartItem(shop["phone"], 1, Dec("11000"), imei_record_id=unit["id"])], payments={"CASH": Dec("11000")})
    pv = reset.preview_reset(["purchases"], shop["day2"], shop["day2"])
    assert pv["blocked"] and im in pv["blocked"]
    with pytest.raises(reset.ResetBlocked):
        reset.run_reset(["purchases"], shop["day2"], shop["day2"], password="admin123", confirm="RESET")
    # together with the sales of that day it works
    reset.run_reset(["purchases", "sales"], shop["day2"], shop["day2"], password="admin123", confirm="RESET")
    assert not [u for u in inventory.available_imeis(shop["phone"]) if u["imei1"] == im]
    _consistent()


def test_repairs_reset_returns_parts_to_stock(shop):
    assert inventory.get_product(shop["display"])["quantity"] == 9
    reset.run_reset(["repairs"], password="admin123", confirm="RESET")
    assert _count("service_jobs") == 0 and inventory.get_product(shop["display"])["quantity"] == 10
    _consistent()


def test_stock_reset_sets_everything_to_zero_with_history(shop):
    reset.run_reset(["stock"], password="admin123", confirm="RESET")
    assert all(p["quantity"] == 0 for p in inventory.list_products())
    assert not inventory.available_imeis(shop["phone"])
    assert _count("stock_movements", "reference='RESET'") >= 3        # history kept as adjustments
    _consistent()


def test_closed_day_figures_are_refreshed_and_closings_can_be_removed(shop):
    before = closing.list_closings()[0]
    reset.run_reset(["expenses"], shop["today"], shop["today"], password="admin123", confirm="RESET")
    after = closing.list_closings()[0]
    assert after["business_date"] == shop["today"] and after["expected_cash"] == before["expected_cash"] + Dec("150.00")
    assert closing.closing_status(shop["today"])["closed"]               # still closed & locked
    reset.run_reset(["closings"], shop["today"], shop["today"], password="admin123", confirm="RESET")
    assert closing.list_closings() == []
    _consistent()


def test_customers_and_suppliers_only_go_when_nothing_refers_to_them(shop):
    spare = parties.create_customer(dict(name="Only Enquiry", phone="9000000099"))
    st = reset.run_reset(["customers", "suppliers"], password="admin123", confirm="RESET")
    assert st["counts"]["customers"] == 1 and st["counts"]["suppliers"] == 0
    assert any("stay" in n for n in st["notes"])
    with pytest.raises(BusinessError):
        parties.get_customer(spare)
    _consistent()


def test_full_reset_keeps_settings_users_and_categories(shop):
    cats = _count("categories")
    st = reset.run_reset(["all"], password="admin123", confirm="RESET")
    assert st["full"]
    for t in ("sales", "sale_items", "payments", "returns", "purchases", "stock_movements", "products", "imei_records",
              "service_jobs", "expenses", "daily_closings", "suppliers", "warranty_claims", "counters"):
        assert _count(t) == 0, t
    assert _count("customers") == 1 and _count("customers", "is_walk_in=1") == 1
    assert _count("categories") == cats and _count("users") >= 1 and _count("audit_logs", "action='DATA_RESET'") == 1
    # the shop starts again cleanly
    pid = inventory.create_product(dict(name="New Charger", category_id=inventory.list_categories()[5]["id"],
                                        purchase_price=100, selling_price=200), opening_qty=5, opening_cost=100)
    assert inventory.get_product(pid)["code"] == "P00001"
    assert sales.quick_sale(pid, 1, 200, "CASH")["invoice_no"].endswith("/00001")
    _consistent()


def test_undo_last_reset_brings_everything_back(shop):
    reset.run_reset(["all"], password="admin123", confirm="RESET")
    info = reset.last_reset()
    assert info and info["full"] and info["summary"]
    with pytest.raises(ValidationError):
        reset.undo_last_reset("wrong")
    reset.undo_last_reset("admin123")
    assert _count("sales") == 3 and _count("products") == 3
    assert inventory.get_product(shop["charger"])["quantity"] == 65
    assert _count("audit_logs", "action='DATA_RESET_UNDONE'") == 1
    _consistent()


def test_only_the_owner_can_reset(shop):
    auth.create_user("manager", "manager123", "MANAGER", "Manager")
    auth.set_current_user(auth.authenticate("manager", "manager123"))
    with pytest.raises(PermissionDenied):
        reset.preview_reset(["sales"])
    with pytest.raises(PermissionDenied):
        reset.run_reset(["sales"], password="manager123", confirm="RESET")


def test_bad_selections_are_rejected(shop):
    with pytest.raises(ValidationError):
        reset.preview_reset([])
    with pytest.raises(ValidationError):
        reset.preview_reset(["sales"], shop["day2"], shop["today"])
    with pytest.raises(ValidationError):
        reset.preview_reset(["sales"], shop["today"], None)
    mods, start, end, full = reset.resolve(["products"], shop["today"], shop["today"])
    assert {"sales", "repairs", "purchases", "stock", "products"} <= mods and start is None and not full
