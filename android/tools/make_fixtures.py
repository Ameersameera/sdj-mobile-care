"""Builds test fixtures for the Android app from the real PC code (run from the repo root).

* calc_vectors.json – bills computed by sdj/core/calc.py (the phone must get the same paise)
* sync_full.json    – a full sync answer for a linked phone (sample shop, GST on, prices incl. GST)
* device.json       – that phone's device id / key / series
* crypto.json       – a PC-sealed envelope and a pairing key, to check the phone's crypto
* shop.db           – the PC database the phone's entries are checked against (check_phone_ops.py)
"""
import base64
import json
import os
import random
import shutil
import sys
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "android/app/src/test/resources/fixtures"
OUT.mkdir(parents=True, exist_ok=True)

tmp = Path(tempfile.mkdtemp())
os.environ["SDJ_DATA_DIR"] = str(tmp / "data")

from sdj import config  # noqa: E402
from sdj.core import auth, parties, phonesync, repairs, sales, settings  # noqa: E402
from sdj.core.calc import LineIn, compute_bill  # noqa: E402
from sdj.core.inventory import create_product, list_categories  # noqa: E402
from sdj.db.init_db import open_database  # noqa: E402

# ---------------------------------------------------------------- calc vectors
rnd = random.Random(20261119)
vectors = []
for n in range(400):
    lines = []
    for _ in range(rnd.randint(1, 5)):
        qty = rnd.randint(1, 4)
        price = rnd.choice([rnd.randint(1, 99999), rnd.randint(100, 3000) * 100, rnd.randint(1, 2000000)])
        disc = rnd.choice([0, 0, rnd.randint(0, qty * price)])
        rate = rnd.choice([0, 500, 1200, 1800, 2800, 1850])
        lines.append(dict(qty=qty, price=price, disc=disc, rate=rate))
    gross_net = sum(l["qty"] * l["price"] - l["disc"] for l in lines)
    bdisc = rnd.choice([0, 0, rnd.randint(0, max(gross_net, 0))])
    flags = dict(gst_enabled=rnd.random() < 0.7, inclusive=rnd.random() < 0.6, round_off=rnd.random() < 0.7)
    b = compute_bill([LineIn(l["qty"], Decimal(l["price"]) / 100, Decimal(l["disc"]) / 100, Decimal(l["rate"]) / 100)
                      for l in lines], Decimal(bdisc) / 100, **flags)
    p = lambda v: int((v * 100).to_integral_value())  # noqa: E731
    vectors.append(dict(lines=lines, bdisc=bdisc, **flags, out=dict(
        gross=p(b.gross), idisc=p(b.item_discount), bdisc=p(b.bill_discount), gross_ex=p(b.gross_ex_tax),
        disc_ex=p(b.discount_ex_tax), taxable=p(b.taxable), gst=p(b.gst), before=p(b.total_before_round),
        round=p(b.round_off), total=p(b.grand_total), cgst=p(b.cgst),
        lines=[dict(gross=p(x.gross), idisc=p(x.item_discount), share=p(x.bill_share), gross_ex=p(x.gross_ex_tax),
                    disc_ex=p(x.discount_ex_tax), taxable=p(x.taxable), gst=p(x.gst), total=p(x.line_total))
               for x in b.lines])))
from sdj.core.calc import amount_in_words  # noqa: E402
words = {str(v): amount_in_words(Decimal(v) / 100) for v in (0, 5, 100, 1999, 125050_50, 10_000_000_00, 123456789_12, 99)}
(OUT / "calc_vectors.json").write_text(json.dumps(dict(bills=vectors, words=words)))

# ---------------------------------------------------------------- sample shop
open_database(config.db_path())
settings.clear_cache()
auth.create_user("admin", "admin123", "ADMIN", "Shop Owner", check_perm=False)
auth.create_user("ravi", "ravi1234", "CASHIER", "Ravi", check_perm=False)
auth.set_current_user(auth.authenticate("admin", "admin123"))
settings.set_many({"gst_enabled": True, "prices_include_gst": True, "staff_discount_limit": 10,
                   "technicians": ["Kumar", "Arun"]})
cats = {c["name"]: c["id"] for c in list_categories()}
imeis = ["356938035643809", "356938035643817", "356938035643825"]
charger = create_product(dict(name="Charger 20W", category_id=cats["Chargers"], purchase_price=250, selling_price=399,
                              barcode="8901234567890", min_stock=3), opening_qty=10, opening_cost=250)
phone = create_product(dict(name="Vivo Y28 4/128", category_id=cats["Smartphones"], purchase_price=12000,
                            selling_price=13999, track_imei=True, warranty_months=12), opening_qty=3, opening_cost=12000,
                       imei_units=[{"imei1": i} for i in imeis])
display = create_product(dict(name="Display Redmi 13C", category_id=cats["Displays"], purchase_price=800,
                              selling_price=1500, gst_rate=28), opening_qty=4, opening_cost=800)
case = create_product(dict(name="Back cover A15", category_id=cats["Cases & Covers"] if "Cases & Covers" in cats else cats["Chargers"],
                           purchase_price=60, selling_price=199, min_stock=5), opening_qty=2, opening_cost=60)
ravi = parties.create_customer(dict(name="Ravi Kumar", phone="9876543210"))
meena = parties.create_customer(dict(name="Meena S", phone="9123456780", opening_balance=500))
sales.create_sale([sales.CartItem(charger, 2, 399)], customer_id=ravi, payments={"CASH": 500})
sales.create_sale([sales.CartItem(case, 1, 199)], pay_full_mode="UPI")
repairs.create_job(dict(customer_name="Meena S", customer_phone="9123456780", brand="Samsung", model="A15",
                        problem="Display broken", labour_charge=300), advance=200)
from sdj.core.expenses import add_expense  # noqa: E402
add_expense("Food", 120, description="Tea")

# ---------------------------------------------------------------- link a phone and take a full sync
code = phonesync.new_link_code(1)["code"]
pc = phonesync.pc_identity()
k = phonesync.pair_key(code, pc)
env = phonesync.seal(k, {"name": "Fixture phone", "hello": "h1", "app": "fixture"}, b"pair")
env["pc"] = pc
ans = phonesync.unseal(k, phonesync.handle_pair(env, "127.0.0.1"), b"pair")
key = base64.b64decode(ans["key"])
req = phonesync.seal(key, {"seq": 1, "ops": [], "token": None, "hashes": {}, "full": True, "app": "fixture"},
                     ans["device"].encode())
req["d"] = ans["device"]
full = phonesync.unseal(key, phonesync.handle_sync(req, "127.0.0.1"), ans["device"].encode())
(OUT / "sync_full.json").write_text(json.dumps(full, indent=1, default=str))
(OUT / "device.json").write_text(json.dumps(dict(device=ans["device"], key=ans["key"], no=ans["no"], pc=ans["pc"],
                                                  shop=ans["shop"], user=ans["user"], seq=1, token=full["token"],
                                                  hashes=full["hashes"]), indent=1))
sample_key = bytes(range(32))
(OUT / "crypto.json").write_text(json.dumps(dict(
    key=base64.b64encode(sample_key).decode(), aad="device-x",
    envelope=phonesync.seal(sample_key, {"hello": "வணக்கம்", "n": 12345, "list": [1, 2, 3]}, b"device-x"),
    code="QT26S-DYCV2", pc="0123456789abcdef0123456789abcdef",
    pair_key=phonesync.pair_key("QT26S-DYCV2", "0123456789abcdef0123456789abcdef").hex())))
from sdj.db.base import dispose_engine  # noqa: E402
dispose_engine()
shutil.copy2(config.db_path(), OUT / "shop.db")
print("fixtures written to", OUT, "| products", len(full["data"]["products"]), "| sales", len(full["data"]["sales"]))
