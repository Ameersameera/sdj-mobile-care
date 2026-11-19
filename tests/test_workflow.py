"""The complete 20-step workflow from the specification (section 23)."""
from datetime import date
from decimal import Decimal as Dec
from pathlib import Path

from sdj.core import closing, expenses, inventory, repairs, reports, sales
from sdj.core.sales import CartItem


def test_spec_section6_charger_example(db, make_product):
    """Section 6: 50 chargers → sale of 1 → 49 left; sale of 2 → 47 left, sold 3, revenue ₹1,200."""
    pid = make_product("Mobile Charger", "Chargers", 250, 400, qty=50)
    sales.quick_sale(pid, 1, 400, "CASH")
    p = inventory.get_product(pid)
    assert p["quantity"] == 49
    today = date.today()
    fig = reports.period_figures(today, today)
    assert fig["f"]["net_counter_sales"] == Dec("400.00")
    assert fig["f"]["counter_cogs"] == Dec("250.00")
    assert fig["f"]["gross_profit"] == Dec("150.00")
    sales.quick_sale(pid, 2, 400, "CASH")
    assert inventory.get_product(pid)["quantity"] == 47
    fig = reports.period_figures(today, today)
    row = next(r for r in reports.product_wise(today, today, fig) if r["product_id"] == pid)
    assert row["sold"] == 3 and row["closing"] == 47 and row["opening"] == 0
    assert row["sales_value"] == Dec("1200.00")
    assert fig["f"]["net_counter_sales"] == Dec("1200.00")


def test_section7_quick_sale_two_products(db, make_product):
    a = make_product("Product A", "Mobile Covers", 200, 500, qty=10)
    b = make_product("Product B", "Power Banks", 600, 1000, qty=10)
    sales.quick_sale(a, 1, 500, "CASH")
    sales.quick_sale(b, 1, 1000, "UPI")
    today = date.today()
    fig = reports.period_figures(today, today)
    assert fig["f"]["net_counter_sales"] == Dec("1500.00")
    pw = {r["name"]: r for r in reports.product_wise(today, today, fig)}
    assert pw["Product A"]["sales_value"] == Dec("500.00")
    assert pw["Product B"]["sales_value"] == Dec("1000.00")
    assert fig["pay_in"]["CASH"] == Dec("500.00") and fig["pay_in"]["UPI"] == Dec("1000.00")


def test_full_workflow(db, make_product, tmp_path):
    today = date.today()
    # 1-3. stock in
    charger = make_product("Mobile Charger 20W", "Chargers", 250, 400, qty=50, min_stock=5)
    earphone = make_product("Wired Earphones", "Earphones", 150, 300, qty=20, min_stock=5)
    display = make_product("Display – Redmi Note 12", "Displays", 800, 1000, qty=10, min_stock=2)
    # 4. sell 2 chargers (quick sale, cash)
    s1 = sales.quick_sale(charger, 2, 400, "CASH")
    assert s1["grand_total"] == Dec("800.00")
    # 5. sell 3 earphones (billing, UPI)
    s2 = sales.create_sale([CartItem(earphone, 3, Dec("300"))], payments={"UPI": Dec("900")})
    assert s2["balance"] == Dec("0.00")
    # 6. sell 1 display over the counter (cash)
    s3 = sales.create_sale([CartItem(display, 1, Dec("1000"))], payments={"CASH": Dec("1000")})
    # 7. stock deducted automatically
    assert inventory.get_product(charger)["quantity"] == 48
    assert inventory.get_product(earphone)["quantity"] == 17
    assert inventory.get_product(display)["quantity"] == 9
    # 8. product-wise sales
    fig = reports.period_figures(today, today)
    pw = {r["product_id"]: r for r in reports.product_wise(today, today, fig)}
    assert (pw[charger]["sold"], pw[charger]["sales_value"]) == (2, Dec("800.00"))
    assert (pw[earphone]["sold"], pw[earphone]["sales_value"]) == (3, Dec("900.00"))
    assert (pw[display]["sold"], pw[display]["sales_value"]) == (1, Dec("1000.00"))
    # 9. profit = (800-500) + (900-450) + (1000-800) = 950
    assert fig["f"]["counter_cogs"] == Dec("1750.00")
    assert fig["f"]["gross_profit"] == Dec("950.00")
    # 10-12. repair job with labour ₹500 and one display charged ₹1,000
    job = repairs.create_job(dict(customer_name="Ravi Kumar", customer_phone="9876543210", brand="Redmi",
                                  model="Note 12", problem="Broken display", labour_charge=500))
    repairs.add_part(job["id"], display, 1, 1000)
    # 13. spare stock deducted immediately
    assert inventory.get_product(display)["quantity"] == 8
    j = repairs.get_job(job["id"])
    assert j["total"] == Dec("1500.00") and j["parts"] == Dec("1000.00") and j["parts_cost"] == Dec("800.00")
    # deliver and collect in cash -> income recognised once
    repairs.deliver_job(job["id"], collect_amount=1500, mode="CASH")
    # 14. service income
    fig = reports.period_figures(today, today)
    f = fig["f"]
    assert f["labour"] == Dec("500.00")
    assert f["parts_revenue"] == Dec("1000.00")
    assert f["parts_cost"] == Dec("800.00")
    assert f["service_income"] == Dec("1500.00")
    assert f["parts_profit"] == Dec("200.00")
    # 15. daily expenses
    expenses.add_expense("Food", 200, description="Tea & snacks", payment_mode="CASH")
    expenses.add_expense("Transportation", 300, description="Parts pickup", payment_mode="UPI")
    # 16. net profit: GP (950 + 200) + labour 500 − expenses 500 = 1,150
    f = reports.period_figures(today, today)["f"]
    assert f["net_sales"] == Dec("3700.00")          # 2,700 counter + 1,000 parts in service
    assert f["cogs"] == Dec("2550.00")
    assert f["gross_profit"] == Dec("1150.00")
    assert f["expenses"] == Dec("500.00")
    assert f["net_operating_profit"] == Dec("1150.00")
    assert f["total_revenue"] == Dec("4200.00")      # never double counts parts revenue
    # cash: 800 + 1000 + 1500 received, 200 cash expense
    cash = reports.cash_position(today, 0)
    assert cash["received"] == Dec("3300.00") and cash["expenses"] == Dec("200.00")
    assert cash["expected"] == Dec("3100.00")
    # 17-18. close the day + PDF
    res = closing.close_day(today, actual_cash=Dec("3100.00"), opening_cash=0)
    assert res["pdf_path"] and Path(res["pdf_path"]).read_bytes()[:4] == b"%PDF"
    assert res["backup"] and Path(res["backup"]).exists()
    saved = closing.saved_report(today)
    assert saved.kv("Cash reconciliation", "Cash difference") == Dec("0.00")
    assert saved.kv("Financial report", "Net operating profit") == Dec("1150.00")
    # stock carried forward, never reset
    assert inventory.get_product(charger)["quantity"] == 48
    # new transactions now roll to the next business date
    from sdj.core.business_day import current_business_date
    from sdj.db.base import session_scope
    with session_scope() as s:
        assert current_business_date(s) > today
    # 19. monthly report
    rep = reports.build_period_report(today.replace(day=1), today)
    assert rep.kv("Summary", "Total sales revenue") == Dec("4200.00")
    assert rep.kv("Summary", "Gross profit") == Dec("1150.00")
    assert rep.kv("Summary", "Net operating profit") == Dec("1150.00")
    assert rep.kv("Summary", "Total service income") == Dec("1500.00")
    assert rep.kv("Summary", "Closing stock valuation") == Dec("20950.00")  # 48×250 + 17×150 + 8×800
    from sdj.output.pdf_report import render_report_pdf
    from sdj.output.excel_export import render_report_csv, render_report_excel
    assert render_report_pdf(rep, tmp_path / "m.pdf").read_bytes()[:4] == b"%PDF"
    assert render_report_excel(rep, tmp_path / "m.xlsx").stat().st_size > 3000
    assert render_report_csv(rep, tmp_path / "m.csv").stat().st_size > 200
    # 20. every figure reconciles
    pw = reports.product_wise(today, today)
    assert sum((r["sales_value"] for r in pw), Dec("0")) == Dec("3700.00")
    assert sum((r["gross_profit"] for r in pw), Dec("0")) == Dec("1150.00")
    for r in pw:
        assert r["opening"] + r["purchased"] - r["sold"] - r["service"] + r["returned"] + r["adjusted"] == r["closing"]
    for doc in (s1, s2, s3):
        pdf = sales.get_sale(doc["id"])
        from sdj.output.pdf_docs import invoice_pdf
        for fmt in ("A4", "THERMAL_80", "THERMAL_58"):
            assert invoice_pdf(pdf, fmt, tmp_path / f"{doc['id']}_{fmt}.pdf").read_bytes()[:4] == b"%PDF"
