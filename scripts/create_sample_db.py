"""Create the SAMPLE / DEMO database (sample_data/sdj_sample.db).

60 days of realistic but fictional activity: products, purchases, sales, repairs, returns, expenses and
daily closings – so every screen and report has something to show. All names and phone numbers are made up.

Demo logins:  admin / admin123   (owner)      staff / staff123   (sales staff)

Usage:  python scripts/create_sample_db.py [output_path] [--days 60]
"""
from __future__ import annotations

import os
import random
import shutil
import sys
import tempfile
from datetime import date, datetime, time, timedelta
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class _Clock:
    current = datetime.now()


class FakeDateTime(datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: D401
        return _Clock.current


def imei(n: int) -> str:
    base = f"86{n:012d}"
    total = 0
    for i, ch in enumerate(reversed(base)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return base + str((10 - total % 10) % 10)


PHONES = [  # name, brand, category, cost, price, warranty months
    ("Redmi 13C 4GB/128GB", "Xiaomi", "Smartphones", 8200, 9499, 12),
    ("Samsung Galaxy A15 5G 6/128", "Samsung", "Smartphones", 15200, 16999, 12),
    ("Realme Narzo N61 4/64", "Realme", "Smartphones", 7300, 8499, 12),
    ("Vivo Y28 5G 6/128", "Vivo", "Smartphones", 12500, 13999, 12),
    ("Nokia 105 Dual SIM", "Nokia", "Feature Phones", 1050, 1399, 12),
    ("iPhone 13 128GB (Refurbished)", "Apple", "Refurbished Phones", 38000, 42999, 6),
    ("Samsung Galaxy M31 (Second-hand)", "Samsung", "Second-hand Phones", 5500, 7499, 3),
]
ACCESSORIES = [  # name, brand, category, cost, price, warranty, opening qty, min
    ("Mobile Charger 20W Fast", "Generic", "Chargers", 250, 400, 6, 50, 10),
    ("Charger 33W Type-C", "Generic", "Chargers", 380, 650, 6, 25, 5),
    ("USB Type-C Cable 1m", "Generic", "USB Cables", 60, 150, 3, 80, 15),
    ("Lightning Cable 1m", "Generic", "USB Cables", 120, 299, 3, 30, 5),
    ("Wired Earphones with Mic", "Generic", "Earphones", 150, 300, 3, 20, 5),
    ("Bluetooth Neckband", "boAt", "Earphones", 550, 999, 6, 15, 4),
    ("TWS Earbuds", "boAt", "Earphones", 700, 1299, 6, 12, 3),
    ("Over-ear Headphones", "Generic", "Headphones", 900, 1599, 6, 6, 2),
    ("Bluetooth Speaker 10W", "Generic", "Bluetooth Speakers", 850, 1499, 6, 8, 2),
    ("Smartwatch Basic", "Noise", "Smartwatches", 1200, 1999, 12, 6, 2),
    ("Back Cover (assorted)", "Generic", "Mobile Covers", 60, 199, 0, 120, 20),
    ("Tempered Glass (assorted)", "Generic", "Tempered Glass", 25, 150, 0, 200, 30),
    ("Power Bank 10000mAh", "Mi", "Power Banks", 650, 1099, 6, 10, 3),
    ("Memory Card 64GB", "SanDisk", "Memory Cards", 380, 599, 12, 15, 4),
    ("OTG Adapter Type-C", "Generic", "Adapters", 40, 120, 0, 40, 8),
]
SPARES = [  # name, category, cost, price, opening qty, min
    ("Display – Redmi Note 12", "Displays", 1450, 2200, 6, 2),
    ("Display – Samsung A15", "Displays", 1900, 2800, 5, 2),
    ("Display – Vivo Y20", "Displays", 1100, 1700, 4, 1),
    ("Battery – Redmi (BN5A)", "Batteries", 450, 900, 10, 3),
    ("Battery – iPhone 11", "Batteries", 1200, 2200, 4, 1),
    ("Charging Port Type-C", "Charging Ports", 90, 350, 25, 5),
    ("Loud Speaker Module", "Speakers", 120, 400, 15, 4),
    ("Microphone", "Microphones", 60, 250, 20, 5),
    ("Rear Camera Module", "Camera Modules", 700, 1400, 4, 1),
    ("Power/Volume Flex Cable", "Flex Cables", 150, 450, 12, 3),
    ("Charging IC", "IC Components", 180, 600, 10, 3),
    ("Battery Connector", "Connectors", 40, 200, 20, 5),
]
FIRST = ["Ravi", "Priya", "Arun", "Meena", "Karthik", "Divya", "Suresh", "Lakshmi", "Vijay", "Anitha", "Prakash",
         "Kavya", "Senthil", "Deepa", "Manoj", "Revathi", "Ganesh", "Nisha"]
LAST = ["Kumar", "R", "S", "Raj", "M", "Devi", "Babu", "K", "Selvam", "Priya"]
PROBLEMS = [("Display broken, touch not working", "Displays"), ("Battery drains quickly", "Batteries"),
            ("Not charging", "Charging Ports"), ("No sound on calls", "Speakers"), ("Mic not working", "Microphones"),
            ("Camera blurry", "Camera Modules"), ("Power button stuck", "Flex Cables"), ("Software hang / restart", None),
            ("Water damage – not switching on", "IC Components")]
DEVICES = [("Redmi", "Note 12"), ("Samsung", "A15"), ("Vivo", "Y20"), ("Apple", "iPhone 11"), ("Realme", "C55"),
           ("Oppo", "A17"), ("Samsung", "M31")]


def build(out_path: Path, days: int = 60, seed: int = 2026) -> Path:
    rnd = random.Random(seed)
    tmp = Path(tempfile.mkdtemp(prefix="sdj_sample_"))
    os.environ["SDJ_DATA_DIR"] = str(tmp)
    from sdj import config
    config._overrides.clear()
    from sdj.core import (auth, business_day, closing, expenses, inventory, parties, purchases, repairs, returns,
                          sales, settings)
    from sdj.core.sales import CartItem
    from sdj.db import models
    from sdj.db.init_db import open_database
    import sdj.core.closing as m_closing
    import sdj.core.sales as m_sales
    import sdj.core.repairs as m_repairs
    import sdj.core.purchases as m_purchases
    import sdj.core.expenses as m_expenses

    open_database(config.db_path())
    today = date.today()
    start = today - timedelta(days=days)
    sim = {"d": start}
    business_day.today = lambda: sim["d"]
    for mod in (models, m_closing, m_sales, m_repairs, m_purchases, m_expenses):
        mod.datetime = FakeDateTime

    def at(d: date, h: int, m: int = 0):
        sim["d"] = d
        _Clock.current = datetime.combine(d, time(h, m, rnd.randint(0, 59)))

    at(start, 9)
    settings.set_many(dict(technicians=["Sathish", "Imran"], default_repair_warranty_days=30, low_stock_default=5,
                           default_opening_cash=2000, auto_backup=False))
    auth.create_user("admin", "admin123", "ADMIN", "Shop Owner (demo)", check_perm=False)
    auth.create_user("staff", "staff123", "CASHIER", "Sales Staff (demo)", check_perm=False)
    auth.set_current_user(auth.authenticate("admin", "admin123"))
    cats = {c["name"]: c["id"] for c in inventory.list_categories()}
    sup_ph = parties.create_supplier(dict(name="Sample Mobile Distributors", contact_person="Sales desk", phone="9000010001"))
    sup_acc = parties.create_supplier(dict(name="Sample Accessories Wholesale", contact_person="Counter", phone="9000010002"))
    sup_sp = parties.create_supplier(dict(name="Sample Spare Parts Hub", contact_person="Parts desk", phone="9000010003",
                                          opening_balance=3500))
    customers = []
    for i in range(18):
        name = f"{FIRST[i]} {LAST[i % len(LAST)]}"
        customers.append(parties.create_customer(dict(name=name, phone=f"90000{20001 + i}",
                                                      opening_balance=1500 if i == 3 else 0)))
    serial = [1000]

    def next_imeis(n):
        out = []
        for _ in range(n):
            serial[0] += 1
            out.append({"imei1": imei(serial[0])})
        return out

    phones, accs, spares = [], [], []
    for name, brand, cat, cost, price, w in PHONES:
        qty = 3 if cost > 30000 else 5
        pid = inventory.create_product(dict(name=name, brand=brand, category_id=cats[cat], purchase_price=cost,
                                            selling_price=price, warranty_months=w, min_stock=2, supplier_id=sup_ph,
                                            barcode=None), qty, cost, next_imeis(qty))
        phones.append((pid, cost, price))
    for name, brand, cat, cost, price, w, qty, mn in ACCESSORIES:
        pid = inventory.create_product(dict(name=name, brand=brand, category_id=cats[cat], purchase_price=cost,
                                            selling_price=price, warranty_months=w, min_stock=mn, supplier_id=sup_acc,
                                            barcode=f"89{rnd.randint(10**10, 10**11 - 1)}"), qty, cost)
        accs.append((pid, cost, price))
    for name, cat, cost, price, qty, mn in SPARES:
        pid = inventory.create_product(dict(name=name, category_id=cats[cat], purchase_price=cost, selling_price=price,
                                            min_stock=mn, supplier_id=sup_sp), qty, cost)
        spares.append((pid, cat, cost, price))
    spare_by_cat = {}
    for pid, cat, cost, price in spares:
        spare_by_cat.setdefault(cat, []).append((pid, price))

    open_jobs = []
    for day_i in range(days + 1):
        d = start + timedelta(days=day_i)
        is_today = d == today
        sunday = d.weekday() == 6
        at(d, 10)
        # restock every ~6 days
        if day_i % 6 == 2:
            lines = []
            for pid, cost, price in rnd.sample(accs, 4):
                p = inventory.get_product(pid)
                if p["quantity"] < p["min_stock"] * 3:
                    lines.append(dict(product_id=pid, quantity=rnd.choice([10, 15, 20]), unit_price=cost, gst_rate=18))
            if lines:
                purchases.create_purchase(sup_acc, lines, supplier_invoice_no=f"SAW-{1000 + day_i}", purchase_date=d,
                                          additional_charges=rnd.choice([0, 100, 150]), paid_amount=0, payment_mode="UPI")
            sl = []
            for pid, cat, cost, price in rnd.sample(spares, 3):
                if inventory.get_product(pid)["quantity"] < 6:
                    sl.append(dict(product_id=pid, quantity=rnd.choice([3, 5]), unit_price=cost, gst_rate=18))
            if sl:
                purchases.create_purchase(sup_sp, sl, supplier_invoice_no=f"SPH-{500 + day_i}", purchase_date=d,
                                          paid_amount=0, payment_mode="CASH")
        if day_i % 12 == 5:
            pl = []
            for pid, cost, price in rnd.sample(phones[:5], 2):
                pl.append(dict(product_id=pid, quantity=2, unit_price=cost, gst_rate=18, imei_units=next_imeis(2)))
            purchases.create_purchase(sup_ph, pl, supplier_invoice_no=f"SMD-{200 + day_i}", purchase_date=d,
                                      paid_amount=0, payment_mode="UPI")
        n_sales = rnd.randint(2, 5) if sunday else rnd.randint(5, 12)
        if is_today:
            n_sales = 6
        for k in range(n_sales):
            at(d, rnd.randint(10, 20), rnd.randint(0, 59))
            r = rnd.random()
            try:
                if r < 0.45:
                    pid, cost, price = rnd.choice(accs)
                    if inventory.get_product(pid)["quantity"] < 1:
                        continue
                    sales.quick_sale(pid, rnd.choice([1, 1, 1, 2]), price, rnd.choice(["CASH", "CASH", "UPI", "UPI", "CARD"]))
                elif r < 0.85:
                    items = []
                    for pid, cost, price in rnd.sample(accs, rnd.randint(1, 3)):
                        if inventory.get_product(pid)["quantity"] >= 1:
                            items.append(CartItem(pid, 1, D(price), D(rnd.choice([0, 0, 0, 20, 50]))))
                    if not items:
                        continue
                    cid = rnd.choice(customers) if rnd.random() < 0.5 else None
                    total = sum(D(i.unit_price) - D(i.discount) for i in items)
                    pays = {rnd.choice(["CASH", "UPI", "UPI", "CARD"]): total}
                    sales.create_sale(items, customer_id=cid, payments=pays)
                else:
                    pid, cost, price = rnd.choice(phones)
                    avail = inventory.available_imeis(pid)
                    if not avail:
                        continue
                    cid = rnd.choice(customers)
                    item = [CartItem(pid, 1, D(price), D(rnd.choice([0, 0, 200, 500])), imei_record_id=avail[0]["id"])]
                    if rnd.random() < 0.85:
                        bill = sales.preview_bill([__import__("sdj.core.calc", fromlist=["LineIn"]).LineIn(1, D(price), item[0].discount, inventory.get_product(pid)["gst_rate"])]).grand_total
                        sales.create_sale(item, customer_id=cid, payments={"UPI": bill - 2000, "CASH": D(2000)} if bill > 4000 else {"CASH": bill})
                    else:
                        sales.create_sale(item, customer_id=cid, payments={"CASH": D(3000)})  # part payment
            except Exception as exc:  # stock ran out etc. – skip like a real shop would
                print("  skip sale:", exc)
        # repairs
        for k in range(rnd.randint(0, 2) if sunday else rnd.randint(1, 4)):
            at(d, rnd.randint(10, 19), rnd.randint(0, 59))
            brand, model = rnd.choice(DEVICES)
            problem, cat = rnd.choice(PROBLEMS)
            ci = rnd.randrange(len(customers))
            c = parties.get_customer(customers[ci])
            labour = rnd.choice([200, 300, 400, 500])
            job = repairs.create_job(dict(customer_name=c["name"], customer_phone=c["phone"], brand=brand, model=model,
                                          problem=problem, technician=rnd.choice(["Sathish", "Imran"]), labour_charge=labour,
                                          estimated_delivery=d + timedelta(days=rnd.choice([0, 1, 2]))),
                                     advance=rnd.choice([0, 0, 200, 500]), advance_mode="CASH")
            if cat and cat in spare_by_cat:
                pid, price = rnd.choice(spare_by_cat[cat])
                if inventory.get_product(pid)["quantity"] >= 1:
                    repairs.add_part(job["id"], pid, 1, price)
            open_jobs.append((job["id"], d))
        # deliver some open jobs (same day or later)
        for jid, jd in list(open_jobs):
            if (d - jd).days >= rnd.choice([0, 1, 2]) and not (is_today and rnd.random() < 0.5):
                at(d, rnd.randint(16, 20), rnd.randint(0, 59))
                j = repairs.get_job(jid)
                bal = j["total"] - j["paid"]
                try:
                    if rnd.random() < 0.1:
                        repairs.set_status(jid, "CANCELLED", reason="Customer did not want the repair", refund_mode="CASH")
                    else:
                        collect = bal if rnd.random() < 0.9 else max(bal - 300, D(0))
                        repairs.deliver_job(jid, collect_amount=max(collect, D(0)), mode=rnd.choice(["CASH", "UPI"]))
                except Exception as exc:
                    print("  skip delivery:", exc)
                open_jobs.remove((jid, jd))
        # expenses
        at(d, 13)
        expenses.add_expense("Food", rnd.choice([80, 120, 150]), description="Tea & snacks", payment_mode="CASH")
        if rnd.random() < 0.4:
            expenses.add_expense("Transportation", rnd.choice([100, 200, 300]), description="Parts pickup", payment_mode="CASH")
        if d.day == 1 or day_i == 0:
            expenses.add_expense("Rent", 12000, description="Shop rent", payment_mode="UPI")
            expenses.add_expense("Electricity", rnd.choice([1800, 2100, 2400]), description="EB bill", payment_mode="UPI")
            expenses.add_expense("Internet", 599, description="Broadband", payment_mode="UPI")
        if d.day == 5:
            expenses.add_expense("Salary", 9000, description="Staff salary", payment_mode="UPI")
        # occasional return / exchange
        if day_i % 9 == 4:
            at(d, 18)
            recent = [s for s in sales.list_sales(d - timedelta(days=3), d) if s["status"] == "COMPLETED"]
            if recent:
                sdet = sales.get_sale(rnd.choice(recent)["id"])
                ln = sdet["lines"][0]
                try:
                    returns.create_return(sdet["id"], [dict(sale_item_id=ln["id"], quantity=1,
                                                            condition=rnd.choice(["RESALABLE", "RESALABLE", "DAMAGED"]))],
                                          reason=rnd.choice(["Defective / not working", "Customer changed mind"]),
                                          refund_mode="CASH")
                except Exception as exc:
                    print("  skip return:", exc)
        # collect some dues & pay suppliers
        if day_i % 7 == 3:
            at(d, 17)
            for crow in parties.list_customers(with_dues_only=True)[:2]:
                parties.collect_customer_payment(crow["id"], min(crow["pending"], D(1000)), "UPI")
            for srow in parties.list_suppliers():
                if srow["due"] > 0:
                    parties.pay_supplier(srow["id"], min(srow["due"], D(15000)), "UPI")
        # close every past day (owner closes at night); today stays open
        if not is_today:
            at(d, 21, 15)
            exp = closing.preview(d).kv("Cash reconciliation", "Expected closing cash")
            diff = D(rnd.choice([0, 0, 0, 0, -50, 20]))
            counted = max(exp + diff, D(0))
            closing.close_day(d, actual_cash=counted, make_pdf=False, make_backup=False,
                              carry_forward=min(D(2000), counted),
                              notes="Cash banked, ₹2,000 float kept" if diff == 0 else "Counted at night")
    # leave a stock alert or two
    at(today, 11)
    from sdj.db.base import dispose_engine
    dispose_engine()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    src = config.db_path()
    import sqlite3
    con = sqlite3.connect(str(src))
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    dst = sqlite3.connect(str(out_path.with_suffix(".tmp")))
    con.backup(dst)
    dst.close()
    con.close()
    os.replace(out_path.with_suffix(".tmp"), out_path)
    shutil.rmtree(tmp, ignore_errors=True)
    return out_path


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    days = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 60
    out = Path(args[0]) if args else ROOT / "sample_data" / "sdj_sample.db"
    p = build(out, days)
    print("Sample database written to", p)
