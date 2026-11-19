"""Returns, exchanges, cancellations, stock protection, credit, duplicates, GST, IMEI, locking."""
from datetime import date
from decimal import Decimal as Dec

import pytest

from sdj.core import closing, expenses, inventory, parties, purchases, repairs, reports, returns, sales, settings
from sdj.core.errors import (BusinessError, DayClosedError, DuplicateError, InsufficientStockError,
                             PermissionDenied, ValidationError)
from sdj.core.sales import CartItem
from tests.conftest import make_imei

TODAY = date.today()


def figs():
    return reports.period_figures(TODAY, TODAY)


# ------------------------------------------------------------------ stock protection
def test_insufficient_stock_blocks_sale_and_changes_nothing(db, make_product):
    pid = make_product("USB Cable", "USB Cables", 60, 150, qty=3)
    with pytest.raises(InsufficientStockError):
        sales.quick_sale(pid, 5, 150, "CASH")
    # multi-line bill where the total of two lines exceeds stock
    with pytest.raises(InsufficientStockError):
        sales.create_sale([CartItem(pid, 2, Dec(150)), CartItem(pid, 2, Dec(150))], payments={"CASH": 600})
    assert inventory.get_product(pid)["quantity"] == 3
    assert sales.list_sales() == []
    assert figs()["f"]["net_counter_sales"] == 0


def test_negative_stock_needs_authorised_adjustment(db, make_product):
    pid = make_product("Speaker", "Speakers", 90, 250, qty=1)
    with pytest.raises(InsufficientStockError):
        inventory.adjust_stock(pid, -2, "count correction")
    inventory.adjust_stock(pid, 4, "found 4 more in godown", unit_cost=90)
    assert inventory.get_product(pid)["quantity"] == 5
    from sdj.core.audit import list_audit
    assert any(a["action"] == "STOCK_ADJUSTED" for a in list_audit())


def test_stock_movement_cannot_be_applied_twice(db, make_product):
    from sqlalchemy.exc import IntegrityError
    from sdj.db.base import session_scope
    from sdj.db.models import Product
    pid = make_product("Adapter", "Adapters", 50, 120, qty=10)
    sale = sales.quick_sale(pid, 1, 120, "CASH")
    detail = sales.get_sale(sale["id"])
    line_id = detail["lines"][0]["id"]
    with pytest.raises(IntegrityError):
        with session_scope() as s:
            p = s.get(Product, pid)
            inventory.apply_movement(s, p, -1, "SALE", business_date=TODAY, ref_type="SALE", ref_id=sale["id"],
                                     ref_line_id=line_id)
    assert inventory.get_product(pid)["quantity"] == 9


def test_duplicate_bill_with_same_client_ref_is_rejected(db, make_product):
    pid = make_product("Tempered Glass", "Tempered Glass", 20, 100, qty=10)
    sales.create_sale([CartItem(pid, 1, Dec(100))], payments={"CASH": 100}, client_ref="bill-123")
    with pytest.raises(DuplicateError):
        sales.create_sale([CartItem(pid, 1, Dec(100))], payments={"CASH": 100}, client_ref="bill-123")
    assert inventory.get_product(pid)["quantity"] == 9
    assert figs()["f"]["net_counter_sales"] == Dec("100.00")


def test_duplicate_supplier_invoice_rejected(db, make_product):
    pid = make_product("Battery", "Batteries", 300, 600)
    sup = parties.create_supplier(dict(name="Chennai Spares"))
    purchases.create_purchase(sup, [dict(product_id=pid, quantity=5, unit_price=300)], supplier_invoice_no="CS-77")
    with pytest.raises(DuplicateError):
        purchases.create_purchase(sup, [dict(product_id=pid, quantity=5, unit_price=300)], supplier_invoice_no="CS-77")
    assert inventory.get_product(pid)["quantity"] == 5


# ------------------------------------------------------------------ cancellations
def test_cancelled_bill_restores_stock_and_voids_payment(db, make_product):
    pid = make_product("Earbuds", "Earphones", 700, 1200, qty=5)
    s1 = sales.create_sale([CartItem(pid, 2, Dec(1200))], payments={"CASH": 2400})
    assert inventory.get_product(pid)["quantity"] == 3
    sales.cancel_sale(s1["id"], "Customer cancelled")
    assert inventory.get_product(pid)["quantity"] == 5
    f = figs()
    assert f["f"]["net_counter_sales"] == 0 and f["f"]["cogs"] == 0
    assert f["pay_in"]["CASH"] == 0
    assert f["counts"]["cancelled_bills"] == 1
    with pytest.raises(BusinessError):
        sales.cancel_sale(s1["id"], "again")          # never reversed twice
    row = next(r for r in reports.product_wise(TODAY, TODAY) if r["product_id"] == pid)
    assert row["sold"] == 0 and row["closing"] == 5


def test_cannot_cancel_after_day_closed_but_admin_can_reopen(db, make_product):
    pid = make_product("Cover", "Mobile Covers", 80, 250, qty=5)
    s1 = sales.quick_sale(pid, 1, 250, "CASH")
    closing.close_day(TODAY, actual_cash=250, opening_cash=0, make_pdf=False, make_backup=False)
    with pytest.raises(DayClosedError):
        sales.cancel_sale(s1["id"], "late cancel")
    with pytest.raises(BusinessError):
        closing.close_day(TODAY, actual_cash=250, make_pdf=False, make_backup=False)
    closing.reopen_day(TODAY, "Wrong bill entered by staff")
    sales.cancel_sale(s1["id"], "late cancel")
    res = closing.close_day(TODAY, actual_cash=0, opening_cash=0, make_pdf=False, make_backup=False)
    rep = closing.saved_report(TODAY)
    assert rep.kv("Cash reconciliation", "Expected closing cash") == Dec("0.00")
    from sdj.core.audit import list_audit
    actions = [a["action"] for a in list_audit()]
    assert "DAY_REOPENED" in actions and actions.count("DAY_CLOSED") == 2
    assert res["id"]


def test_locked_closing_row_cannot_be_edited_directly(db):
    from sqlalchemy import text
    from sdj.db.base import session_scope
    closing.close_day(TODAY, actual_cash=0, opening_cash=0, make_pdf=False, make_backup=False)
    with pytest.raises(Exception):
        with session_scope() as s:
            s.execute(text("UPDATE daily_closings SET actual_cash = 999 WHERE business_date = :d"), {"d": TODAY.isoformat()})
    with pytest.raises(Exception):
        with session_scope() as s:
            s.execute(text("DELETE FROM daily_closings"))  # row exists -> trigger must block


# ------------------------------------------------------------------ partial payments / credit
def test_partial_payment_credit_sale_and_collection(db, make_product):
    pid = make_product("Smartwatch", "Smartwatches", 1500, 2500, qty=3)
    walk_in_err = None
    try:
        sales.create_sale([CartItem(pid, 1, Dec(2500))], payments={"CASH": 1000})
    except ValidationError as e:
        walk_in_err = e
    assert walk_in_err is not None                    # credit needs a named customer
    cid = parties.create_customer(dict(name="Meena", phone="9000000001"))
    s1 = sales.create_sale([CartItem(pid, 1, Dec(2500))], customer_id=cid, payments={"CASH": 1000, "UPI": 500})
    assert s1["balance"] == Dec("1000.00")
    assert parties.customer_balance(cid) == Dec("1000.00")
    assert figs()["f"]["credit_sales"] == Dec("1000.00")
    with pytest.raises(ValidationError):
        sales.collect_sale_payment(s1["id"], 1500, "CASH")   # cannot over-collect
    parties.collect_customer_payment(cid, 600, "UPI")
    assert parties.customer_balance(cid) == Dec("400.00")
    sales.collect_sale_payment(s1["id"], 400, "CASH")
    assert parties.customer_balance(cid) == Dec("0.00")
    f = figs()
    assert f["f"]["due_collections"] == Dec("1000.00")
    assert f["pay_in"]["CASH"] == Dec("1400.00") and f["pay_in"]["UPI"] == Dec("1100.00")
    assert f["f"]["net_counter_sales"] == Dec("2500.00")   # revenue counted once, not per payment
    led = parties.customer_ledger(cid)
    assert led["balance"] == Dec("0.00") and led["total_debit"] == Dec("2500.00")


def test_overpayment_is_rejected(db, make_product):
    pid = make_product("Charger", "Chargers", 250, 400, qty=5)
    with pytest.raises(ValidationError):
        sales.create_sale([CartItem(pid, 1, Dec(400))], payments={"CASH": 500})


# ------------------------------------------------------------------ returns & exchanges
def test_full_return_with_refund(db, make_product):
    pid = make_product("Bluetooth Speaker", "Bluetooth Speakers", 900, 1500, qty=4)
    s1 = sales.create_sale([CartItem(pid, 2, Dec(1500))], payments={"CASH": 3000})
    r = returns.create_return(s1["id"], [dict(sale_item_id=sales.get_sale(s1["id"])["lines"][0]["id"], quantity=2,
                                              condition="RESALABLE")], reason="Customer changed mind", refund_mode="CASH")
    assert r["value"] == Dec("3000.00") and r["refund"] == Dec("3000.00")
    assert inventory.get_product(pid)["quantity"] == 4
    f = figs()["f"]
    assert f["net_counter_sales"] == 0 and f["counter_cogs"] == 0 and f["gross_profit"] == 0
    assert figs()["refunds_out"]["CASH"] == Dec("3000.00")
    assert reports.cash_position(TODAY, 0)["expected"] == Dec("0.00")
    assert sales.get_sale(s1["id"])["balance"] == Dec("0.00")
    with pytest.raises(ValidationError):   # nothing left to return
        returns.create_return(s1["id"], [dict(sale_item_id=sales.get_sale(s1["id"])["lines"][0]["id"], quantity=1)],
                              reason="again")


def test_partial_return_damaged_item(db, make_product):
    pid = make_product("Power Bank 10000", "Power Banks", 600, 1000, qty=5)
    s1 = sales.create_sale([CartItem(pid, 3, Dec(1000))], payments={"UPI": 3000})
    line = sales.get_sale(s1["id"])["lines"][0]["id"]
    r = returns.create_return(s1["id"], [dict(sale_item_id=line, quantity=1, condition="DAMAGED")],
                              reason="Defective / not working", refund_mode="UPI")
    assert r["refund"] == Dec("1000.00")
    assert inventory.get_product(pid)["quantity"] == 2           # damaged unit does not go back to stock
    f = figs()["f"]
    assert f["net_counter_sales"] == Dec("2000.00")
    assert f["counter_cogs"] == Dec("1800.00")                   # damaged cost stays as a loss
    assert f["damaged_cost"] == Dec("600.00")
    assert len(returns.damaged_items()) == 1


def test_return_on_credit_sale_reduces_dues_not_cash(db, make_product):
    pid = make_product("Headphones", "Headphones", 800, 1400, qty=2)
    cid = parties.create_customer(dict(name="Arun", phone="9000000002"))
    s1 = sales.create_sale([CartItem(pid, 2, Dec(1400))], customer_id=cid, payments={"CASH": 1400})
    line = sales.get_sale(s1["id"])["lines"][0]["id"]
    prev = returns.preview_return(s1["id"], [dict(sale_item_id=line, quantity=1)])
    assert prev["refundable"] == Dec("0.00") and prev["due_reduction"] == Dec("1400.00")
    r = returns.create_return(s1["id"], [dict(sale_item_id=line, quantity=1)], reason="Customer changed mind")
    assert r["refund"] == Dec("0.00")
    assert parties.customer_balance(cid) == Dec("0.00")


def test_exchange_with_difference_paid(db, make_product):
    old = make_product("Earphones Basic", "Earphones", 150, 300, qty=5)
    new = make_product("Earphones Pro", "Earphones", 400, 700, qty=5)
    s1 = sales.create_sale([CartItem(old, 1, Dec(300))], payments={"CASH": 300})
    line = sales.get_sale(s1["id"])["lines"][0]["id"]
    r = returns.create_return(s1["id"], [dict(sale_item_id=line, quantity=1)], reason="Exchange for another model",
                              exchange_items=[CartItem(new, 1, Dec(700))], exchange_payments={"CASH": 400})
    assert r["exchange_invoice_no"] and r["exchange_credit"] == Dec("300.00") and r["refund"] == 0
    assert inventory.get_product(old)["quantity"] == 5
    assert inventory.get_product(new)["quantity"] == 4
    f = figs()
    assert f["f"]["net_counter_sales"] == Dec("700.00")
    assert f["f"]["gross_profit"] == Dec("300.00")
    assert f["pay_in"]["CASH"] == Dec("700.00")                  # 300 original + 400 difference
    assert sales.get_sale(r["exchange_sale_id"])["balance"] == Dec("0.00")
    assert sales.get_sale(s1["id"])["balance"] == Dec("0.00")


def test_exchange_for_cheaper_item_refunds_difference(db, make_product):
    a = make_product("Cover Premium", "Mobile Covers", 200, 600, qty=3)
    b = make_product("Cover Basic", "Mobile Covers", 60, 200, qty=3)
    s1 = sales.create_sale([CartItem(a, 1, Dec(600))], payments={"UPI": 600})
    line = sales.get_sale(s1["id"])["lines"][0]["id"]
    r = returns.create_return(s1["id"], [dict(sale_item_id=line, quantity=1)], reason="Exchange for another model",
                              refund_mode="CASH", exchange_items=[CartItem(b, 1, Dec(200))])
    assert r["exchange_credit"] == Dec("200.00") and r["refund"] == Dec("400.00")
    f = figs()
    assert f["f"]["net_counter_sales"] == Dec("200.00")
    assert f["refunds_out"]["CASH"] == Dec("400.00")


# ------------------------------------------------------------------ service jobs
def test_service_job_parts_removed_and_cancel_restores_stock(db, make_product):
    batt = make_product("Battery iPhone 11", "Batteries", 1200, 2000, qty=3)
    port = make_product("Charging Port Type-C", "Charging Ports", 100, 350, qty=5)
    job = repairs.create_job(dict(customer_name="Priya", customer_phone="9000000003", brand="Apple", model="iPhone 11",
                                  problem="Battery drains", labour_charge=300), advance=500, advance_mode="UPI")
    i1 = repairs.add_part(job["id"], batt, 1)
    repairs.add_part(job["id"], port, 2, 300)
    assert inventory.get_product(batt)["quantity"] == 2 and inventory.get_product(port)["quantity"] == 3
    repairs.remove_part(job["id"], i1)
    assert inventory.get_product(batt)["quantity"] == 3
    assert repairs.get_job(job["id"])["total"] == Dec("900.00")   # 300 labour + 2×300
    # no income until the job is completed
    assert figs()["f"]["service_income"] == 0
    repairs.set_status(job["id"], "CANCELLED", reason="Customer took phone back", refund_mode="UPI")
    assert inventory.get_product(port)["quantity"] == 5
    j = repairs.get_job(job["id"])
    assert j["paid"] == Dec("0.00") and j["status"] == "CANCELLED"
    f = figs()
    assert f["f"]["service_income"] == 0
    assert f["pay_in"]["UPI"] == Dec("500.00") and f["refunds_out"]["UPI"] == Dec("500.00")
    with pytest.raises(BusinessError):
        repairs.add_part(job["id"], port, 1)


def test_service_spare_revenue_never_double_counted(db, make_product):
    disp = make_product("Display Samsung A15", "Displays", 800, 1000, qty=2)
    job = repairs.create_job(dict(customer_name="Kumar", customer_phone="9000000004", brand="Samsung", model="A15",
                                  problem="Screen broken", labour_charge=500))
    repairs.add_part(job["id"], disp, 1, 1000)
    repairs.set_status(job["id"], "READY")
    repairs.deliver_job(job["id"], collect_amount=1000, mode="CASH")   # 500 left as customer due
    f = figs()["f"]
    assert f["parts_revenue"] == Dec("1000.00") and f["labour"] == Dec("500.00")
    assert f["total_revenue"] == Dec("1500.00")
    assert f["net_sales"] == Dec("1000.00") and f["cogs"] == Dec("800.00")
    assert f["net_operating_profit"] == Dec("700.00")
    cid = repairs.get_job(job["id"])["customer_id"]
    assert parties.customer_balance(cid) == Dec("500.00")
    parties.collect_customer_payment(cid, 500, "CASH")
    assert parties.customer_balance(cid) == Dec("0.00")
    assert reports.period_figures(TODAY, TODAY)["f"]["total_revenue"] == Dec("1500.00")


def test_excess_advance_refunded_on_delivery(db):
    job = repairs.create_job(dict(customer_name="Lata", customer_phone="9000000005", brand="Vivo", model="Y20",
                                  problem="Software issue", labour_charge=300), advance=500, advance_mode="CASH")
    out = repairs.deliver_job(job["id"])
    assert out["refund"] == Dec("200.00") and out["balance"] == Dec("0.00")
    assert reports.cash_position(TODAY, 0)["expected"] == Dec("300.00")


# ------------------------------------------------------------------ purchases & weighted average
def test_purchase_weighted_average_landed_cost_and_supplier_dues(db, make_product):
    pid = make_product("Charger 33W", "Chargers", 250, 450, qty=10)          # 10 @ 250
    sup = parties.create_supplier(dict(name="Madurai Wholesale", phone="9000000010"))
    p = purchases.create_purchase(sup, [dict(product_id=pid, quantity=10, unit_price=280, gst_rate=18)],
                                  additional_charges=100, paid_amount=1000, payment_mode="CASH")
    # GST off -> GST paid is part of cost: (2800 + 504 + 100) / 10 = 340.40 landed
    assert p["total"] == Dec("3404.00")
    prod = inventory.get_product(pid)
    assert prod["quantity"] == 20
    assert prod["avg_cost"] == Dec("295.2000")                              # (10×250 + 10×340.40) / 20
    assert parties.list_suppliers()[0]["due"] == Dec("2404.00")
    parties.pay_supplier(sup, 2404, "UPI")
    assert parties.list_suppliers()[0]["due"] == Dec("0.00")
    s1 = sales.quick_sale(pid, 1, 450, "CASH")
    assert s1["cogs"] == Dec("295.20")
    with pytest.raises(ValidationError):         # a reason is mandatory for cancellation
        purchases.cancel_purchase(p["id"], "")


def test_purchase_cancel_reverses_stock(db, make_product):
    pid = make_product("Flex Cable", "Flex Cables", 50, 200)
    sup = parties.create_supplier(dict(name="Spare Hub"))
    p = purchases.create_purchase(sup, [dict(product_id=pid, quantity=6, unit_price=55)], paid_amount=330)
    purchases.cancel_purchase(p["id"], "Wrong items delivered")
    assert inventory.get_product(pid)["quantity"] == 0
    assert figs()["supplier_out"]["CASH"] == 0


# ------------------------------------------------------------------ GST
def test_gst_inclusive_pricing_and_bill_discount(db, make_product):
    settings.set_many({"gst_enabled": True, "prices_include_gst": True, "round_off": True})
    pid = make_product("Phone Case", "Mobile Covers", 100, 590, qty=10, gst_rate=18)
    s1 = sales.create_sale([CartItem(pid, 2, Dec(590))], bill_discount=Dec(180), payments={"CASH": 1000})
    d = sales.get_sale(s1["id"])
    assert d["grand_total"] == Dec("1000.00")
    assert d["taxable_amount"] == Dec("847.46") and d["gst_amount"] == Dec("152.54")
    f = figs()["f"]
    assert f["gross_ex"] == Dec("1000.00")       # 1180 incl. GST -> 1000 excl.
    assert f["discount_ex"] == Dec("152.54")
    assert f["net_counter_sales"] == Dec("847.46")
    assert f["gst_output"] == Dec("152.54")


def test_gst_exclusive_with_round_off(db, make_product):
    settings.set_many({"gst_enabled": True, "prices_include_gst": False, "round_off": True})
    pid = make_product("Cable", "USB Cables", 40, 99, qty=10, gst_rate=18)
    s1 = sales.create_sale([CartItem(pid, 1, Dec(99))], payments={"CASH": 117})
    d = sales.get_sale(s1["id"])
    assert d["taxable_amount"] == Dec("99.00") and d["gst_amount"] == Dec("17.82")
    assert d["round_off"] == Dec("0.18") and d["grand_total"] == Dec("117.00")


# ------------------------------------------------------------------ IMEI & warranty
def test_imei_sale_warranty_and_return_to_stock(db, make_product):
    imeis = [make_imei(i) for i in range(1, 4)]
    pid = make_product("Redmi 13C 4/128", "Smartphones", 8500, 9999, qty=3, imeis=imeis, warranty_months=12)
    avail = inventory.available_imeis(pid)
    assert len(avail) == 3
    with pytest.raises(ValidationError):
        sales.create_sale([CartItem(pid, 1, Dec(9999))], payments={"CASH": 9999})   # IMEI must be chosen
    s1 = sales.create_sale([CartItem(pid, 1, Dec(9999), imei_record_id=avail[0]["id"])], payments={"CASH": 9999})
    from sdj.core import warranty
    rec = warranty.search_imei(imeis[0])[0]
    assert rec["status"] == "SOLD" and rec["invoice_no"] == s1["invoice_no"]
    assert rec["warranty_expiry"] is not None
    hist = warranty.imei_history(imeis[0])
    assert [e["event"] for e in hist][:2] == ["Added as opening stock", "Sold"]
    line = sales.get_sale(s1["id"])["lines"][0]["id"]
    returns.create_return(s1["id"], [dict(sale_item_id=line, quantity=1)], reason="Defective / not working")
    assert warranty.search_imei(imeis[0])[0]["status"] == "IN_STOCK"
    assert inventory.get_product(pid)["quantity"] == 3
    claim = warranty.create_claim(imeis[1], "Not charging")
    assert claim["claim_no"].startswith("WC-")


def test_imei_validation(db, make_product):
    with pytest.raises(ValidationError):
        make_product("Phone X", "Smartphones", 5000, 6000, qty=2, imeis=[make_imei(9)])        # count mismatch
    with pytest.raises(ValidationError):
        make_product("Phone Y", "Smartphones", 5000, 6000, qty=1, imeis=["356938035643801"])  # bad check digit
    make_product("Phone Z", "Smartphones", 5000, 6000, qty=1, imeis=[make_imei(10)])
    with pytest.raises(ValidationError):
        make_product("Phone Z2", "Smartphones", 5000, 6000, qty=1, imeis=[make_imei(10)])     # already in stock


def test_accessories_do_not_need_imei(db, make_product):
    pid = make_product("Memory Card 64GB", "Memory Cards", 350, 550, qty=5)
    s1 = sales.create_sale([CartItem(pid, 2, Dec(550))], payments={"CARD": 1100})
    assert s1["balance"] == 0


# ------------------------------------------------------------------ expenses / business day
def test_expense_edit_void_and_future_date(db):
    eid = expenses.add_expense("Electricity", 1800, payment_mode="UPI")
    expenses.update_expense(eid, category="Electricity", amount=1850, payment_mode="UPI")
    assert figs()["f"]["expenses"] == Dec("1850.00")
    expenses.void_expense(eid, "entered twice")
    assert figs()["f"]["expenses"] == 0
    from datetime import timedelta
    with pytest.raises(ValidationError):
        expenses.add_expense("Rent", 100, expense_date=TODAY + timedelta(days=3))


def test_closing_requires_earlier_days_and_rolls_business_date(db, make_product):
    from datetime import timedelta
    from sdj.core.business_day import current_business_date
    from sdj.db.base import session_scope
    pid = make_product("Charger", "Chargers", 250, 400, qty=10)
    closing.close_day(TODAY, actual_cash=0, opening_cash=0, make_pdf=False, make_backup=False)
    s2 = sales.quick_sale(pid, 1, 400, "CASH")             # day closed -> goes to next business date
    detail = sales.get_sale(s2["id"])
    assert detail["business_date"] == TODAY + timedelta(days=1)
    with session_scope() as s:
        assert current_business_date(s) == TODAY + timedelta(days=1)
    assert closing.suggested_opening_cash(TODAY + timedelta(days=1)) == Dec("0.00")


# ------------------------------------------------------------------ permissions & users
def test_role_permissions(db, make_product):
    from sdj.core import auth
    auth.create_user("ravi", "staff123", "CASHIER", "Ravi")
    auth.set_current_user(auth.authenticate("ravi", "staff123"))
    pid_err = None
    with pytest.raises(PermissionDenied):
        closing.close_day(TODAY, actual_cash=0, make_pdf=False, make_backup=False)
    with pytest.raises(PermissionDenied):
        inventory.adjust_stock(1, 5, "x")
    with pytest.raises(PermissionDenied):
        auth.create_user("x", "123456", "ADMIN")
    assert pid_err is None
    auth.set_current_user(auth.authenticate("admin", "admin123"))
    with pytest.raises(BusinessError):
        auth.authenticate("admin", "wrong-pass")


def test_password_hashing():
    from sdj.core.auth import hash_password, verify_password
    h = hash_password("secret99")
    assert h.startswith("pbkdf2_sha256$") and "secret99" not in h
    assert verify_password("secret99", h) and not verify_password("secret98", h)
