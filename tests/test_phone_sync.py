"""Phone link: pairing, encrypted sync, entries saved exactly once, and the conflict rules."""
import base64
import json
import os
import socket
import sys
import sqlite3
import urllib.request
import uuid
from datetime import date, datetime, timedelta

import pytest

from conftest import make_imei
from sdj import config
from sdj.core import closing, expenses, parties, phonesync, repairs, sales, settings
from sdj.core.numbering import financial_year_label
from sdj.core.phonesync import pair_key, seal, unseal


class FakePhone:
    """Speaks the same protocol as the Android app."""

    def __init__(self):
        self.seq, self.token, self.hashes, self.data = 0, None, {}, {}
        self.inv = self.job = 0

    def pair(self, code, name="Test phone"):
        pc = phonesync.pc_identity()
        k = pair_key(code, pc)
        hello = uuid.uuid4().hex
        env = seal(k, {"name": name, "hello": hello, "app": "test"}, b"pair")
        env["pc"] = pc
        ans = unseal(k, phonesync.handle_pair(env, "127.0.0.1"), b"pair")
        assert ans["hello"] == hello and ans["ok"]
        self.device, self.key, self.no, self.user = ans["device"], base64.b64decode(ans["key"]), ans["no"], ans["user"]
        return ans

    def envelope(self, ops=(), full=False, seq=None):
        self.seq = seq if seq is not None else self.seq + 1
        req = {"seq": self.seq, "ops": list(ops), "token": self.token, "hashes": self.hashes, "full": full, "app": "t"}
        env = seal(self.key, req, self.device.encode())
        env["d"] = self.device
        return env

    def read(self, ans):
        ans = unseal(self.key, ans, self.device.encode())
        self.token = ans["token"]
        if "data" in ans:
            self.data.update(ans["data"])
            self.hashes = ans["hashes"]
        return ans

    def sync(self, ops=(), full=False, seq=None):
        return self.read(phonesync.handle_sync(self.envelope(ops, full, seq), "127.0.0.1"))

    # ---- entries, built the way the app builds them
    def bill(self, items, pay, day=None, cust=None, kind="BILL", total=None):
        self.inv += 1
        d = day or date.today()
        no = f"SDJ-P{self.no}/{financial_year_label(d)}/{self.inv:05d}"
        op = dict(id=uuid.uuid4().hex, k="sale.new", at=datetime.now().isoformat(), d=d.isoformat(),
                  u=self.user["id"], ref=uuid.uuid4().hex, no=no, type=kind, cust=cust, items=items, bdisc=0,
                  pay=pay, notes="")
        if total is not None:
            op["total"] = total
        return op

    def op(self, kind, day=None, **kw):
        return dict(id=uuid.uuid4().hex, k=kind, at=datetime.now().isoformat(),
                    d=(day or date.today()).isoformat(), u=self.user["id"], **kw)


def _consistent():
    con = sqlite3.connect(config.db_path())
    try:
        for pid, qty in con.execute("SELECT id, quantity FROM products"):
            total = con.execute("SELECT COALESCE(SUM(qty_change),0) FROM stock_movements WHERE product_id=?",
                                (pid,)).fetchone()[0]
            assert total == qty, (pid, qty, total)
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        con.close()


@pytest.fixture
def linked(db, make_product):
    charger = make_product("Charger 20W", "Chargers", 250, 400, qty=10)
    mobile = make_product("Vivo Y28", "Smartphones", 12000, 14000, qty=1, imeis=[make_imei(7)])
    cust = parties.create_customer(dict(name="Ravi Kumar", phone="9000000002"))
    phone = FakePhone()
    code = phonesync.new_link_code(1)["code"]
    phone.pair(code)
    phone.sync(full=True)
    return dict(phone=phone, charger=charger, mobile=mobile, cust=cust)


def _alerts(kind=None):
    return [a for a in phonesync.list_alerts() if kind is None or a["kind"] == kind]


def test_pairing_is_one_time_and_needs_the_right_code(db):
    phone = FakePhone()
    info = phonesync.new_link_code(1)
    assert len(info["code"]) == 10 and info["display"][5] == "-" and info["qr"].startswith("SDJLINK:1;")
    with pytest.raises(phonesync.SyncError):
        phone.pair("WRONGCODE2")
    ans = phone.pair(info["display"].lower())          # dash / lower case are fine
    assert ans["no"] == 1 and ans["user"]["username"] == "admin"
    with pytest.raises(phonesync.SyncError):
        FakePhone().pair(info["code"])                   # one-time
    devs = phonesync.list_devices()
    assert len(devs) == 1 and devs[0]["series"] == "P1"


def test_full_sync_sends_shop_data(linked):
    d = linked["phone"].data
    assert {"products", "imeis", "customers", "sales", "jobs", "expenses", "dash", "shop", "users"} <= set(d)
    assert any(p["name"] == "Vivo Y28" and p["imei"] for p in d["products"])
    assert d["shop"]["inv_series"] == "SDJ-P1" and d["shop"]["job_series"] == "JOB-P1"
    assert d["shop"]["shop_address"] == "" and d["shop"]["shop_gstin"] == ""      # never invented
    admin = next(u for u in d["users"] if u["username"] == "admin")
    assert admin["hash"].startswith("pbkdf2_sha256$") and "billing" in admin["perms"]
    again = linked["phone"].sync()
    assert "data" not in again                           # nothing changed → nothing sent


def test_phone_bill_is_saved_exactly_once(linked):
    phone, ch = linked["phone"], linked["charger"]
    op = phone.bill([dict(p=ch, q=2, price=40000, disc=0)], {"CASH": 80000}, total=80000)
    r = phone.sync([op])["results"][0]
    assert r["st"] == "DONE" and r["res"]["no"].startswith("SDJ-P1/") and r["res"]["total"] == 80000
    again = phone.sync([op])["results"][0]               # answer was lost → the phone sends it again
    assert again["st"] == "DONE" and again["res"]["id"] == r["res"]["id"]
    rows = sales.list_sales()
    assert len(rows) == 1 and rows[0]["invoice_no"] == r["res"]["no"]
    assert next(p for p in phone.data["products"] if p["id"] == ch)["qty"] == 8
    assert any(s["no"] == r["res"]["no"] for s in phone.data["sales"])
    with pytest.raises(phonesync.SyncError):
        phone.sync([], seq=1)                            # old request replayed
    _consistent()


def test_last_piece_and_imei_sold_on_both_sides(linked):
    phone, mob = linked["phone"], linked["mobile"]
    unit = next(i for i in phone.data["imeis"] if i["p"] == mob)
    # the PC sells the only phone while the mobile is offline …
    sales.create_sale([sales.CartItem(mob, 1, 14000, imei_record_id=unit["id"])], customer_id=linked["cust"],
                      pay_full_mode="CASH")
    # … and the mobile sells the same unit
    op = phone.bill([dict(p=mob, q=1, price=1400000, imei_id=unit["id"], imei=unit["i1"])], {"UPI": 1400000},
                    cust={"id": linked["cust"]})
    r = phone.sync([op])["results"][0]
    assert r["st"] == "DONE"                             # the bill is kept – the customer has the phone
    kinds = {a["kind"] for a in _alerts()}
    assert {"NEGATIVE_STOCK", "IMEI"} <= kinds
    sale = sales.get_sale(r["res"]["id"])
    assert sale["lines"][0]["imei"] == unit["i1"] and sale["lines"][0]["imei_record_id"] is None
    assert next(p for p in phone.data["products"] if p["id"] == mob)["qty"] == -1
    _consistent()


def test_entry_for_a_closed_day_asks_to_update_the_closing(linked):
    phone, ch = linked["phone"], linked["charger"]
    today = date.today()
    closing.close_day(today, 0, make_pdf=False, make_backup=False)
    op = phone.bill([dict(p=ch, q=1, price=40000)], {"CASH": 40000}, day=today)
    assert phone.sync([op])["results"][0]["st"] == "DONE"
    sale = sales.list_sales()[0]
    assert sale["business_date"] == today                # keeps the day it was made on
    alert = _alerts("CLOSED_DAY")[0]
    assert alert["can_update_closing"] and alert["ref_date"] == today
    phonesync.update_closing_from_alert(alert["id"])
    rec = next(c for c in closing.list_closings() if c["business_date"] == today)
    assert rec["expected_cash"] == 400
    assert not _alerts("CLOSED_DAY")


def test_bill_changed_on_pc_and_phone_becomes_a_conflict(linked):
    phone, ch = linked["phone"], linked["charger"]
    r = sales.create_sale([sales.CartItem(ch, 1, 400)], customer_id=linked["cust"], pay_full_mode="CASH")
    phone.sync()
    seen = next(s for s in phone.data["sales"] if s["id"] == r["id"])
    sales.amend_sale(r["id"], [sales.CartItem(ch, 2, 400)], customer_id=linked["cust"], payments={"CASH": 800})
    edit = phone.op("sale.edit", ref=uuid.uuid4().hex, target={"id": seen["id"]},
                    base={"no": seen["no"], "paid": seen["paid"]}, cust={"id": linked["cust"]},
                    items=[dict(p=ch, q=3, price=40000)], bdisc=0, pay={"CASH": 120000})
    res = phone.sync([edit])["results"][0]
    assert res["st"] == "CONFLICT"
    alert = _alerts("BILL_CONFLICT")[0]
    assert alert["can_use_phone"]
    phonesync.use_phone_version(alert["id"])
    current = [s for s in sales.list_sales() if s["invoice_no"] == seen["no"]][0]
    assert current["grand_total"] == 1200 and current["status"] == "COMPLETED"
    _consistent()


def test_phone_can_edit_and_cancel_its_own_bills_before_syncing(linked):
    phone, ch = linked["phone"], linked["charger"]
    new = phone.bill([dict(p=ch, q=1, price=40000)], {"CASH": 40000})
    edit = phone.op("sale.edit", ref=uuid.uuid4().hex, target={"ref": new["ref"]}, cust=None,
                    items=[dict(p=ch, q=2, price=40000)], bdisc=0, pay={"CASH": 80000})
    second = phone.bill([dict(p=ch, q=1, price=40000)], {"CASH": 40000})
    cancel = phone.op("sale.cancel", target={"ref": second["ref"]}, reason="Customer changed mind")
    results = phone.sync([new, edit, second, cancel])["results"]
    assert [r["st"] for r in results] == ["DONE"] * 4
    rows = {s["invoice_no"]: s for s in sales.list_sales()}
    assert rows[new["no"]]["grand_total"] == 800 and rows[new["no"]]["status"] == "COMPLETED"
    assert rows[second["no"]]["status"] == "CANCELLED"
    assert next(p for p in phone.data["products"] if p["id"] == ch)["qty"] == 8
    _consistent()


def test_expense_customer_payment_and_repair_job(linked):
    phone, cust = linked["phone"], linked["cust"]
    credit = phone.bill([dict(p=linked["charger"], q=1, price=40000)], {"CASH": 10000}, cust={"id": cust})
    exp = phone.op("exp.new", ref=uuid.uuid4().hex, cat="Food", amount=15000, mode="CASH", desc="Tea")
    pay = phone.op("cust.pay", ref=uuid.uuid4().hex, cust={"id": cust}, amount=50000, mode="UPI")   # due is 300
    phone.job += 1
    job = phone.op("job.new", ref=uuid.uuid4().hex, no=f"JOB-P1-{phone.job:04d}", customer_name="Meena",
                   customer_phone="9000000009", brand="Samsung", model="A15", imei="", problem="Display broken",
                   labour=50000, adv=20000, adv_mode="CASH")
    results = phone.sync([credit, exp, pay, job])["results"]
    assert [r["st"] for r in results] == ["DONE"] * 4
    assert any(e["category"] == "Food" for e in expenses.list_expenses())
    assert parties.customer_balance(cust) == 0
    assert _alerts("EXTRA_PAYMENT")
    jref = {"ref": job["ref"]}
    part = phone.op("job.part", ref=uuid.uuid4().hex, target=jref, p=linked["charger"], q=1, price=40000)
    ready = phone.op("job.status", target=jref, status="READY")
    deliver = phone.op("job.deliver", target=jref, collect=70000, mode="CASH")
    assert [r["st"] for r in phone.sync([part, ready, deliver])["results"]] == ["DONE"] * 3
    j = repairs.get_job(results[3]["res"]["id"])
    assert j["job_no"] == "JOB-P1-0001" and j["status"] == "DELIVERED" and j["balance"] == 0 and j["total"] == 900
    _consistent()


def test_removed_phone_cannot_sync(linked):
    phone = linked["phone"]
    dev = phonesync.list_devices()[0]
    with pytest.raises(Exception):
        phonesync.remove_device(dev["id"], "wrong-password")
    phonesync.remove_device(dev["id"], "admin123")
    with pytest.raises(phonesync.SyncError) as e:
        phone.sync()
    assert e.value.code == "removed"


def test_network_service_hello_sync_and_discovery(linked):
    from sdj.core.syncserver import SyncServer
    srv = SyncServer(port=0, udp_port=0, host="127.0.0.1")
    assert srv.start()
    try:
        base = f"http://127.0.0.1:{srv.port}"
        hello = json.loads(urllib.request.urlopen(base + "/sdj/hello", timeout=5).read())
        assert hello["pc"] == phonesync.pc_identity()
        page = urllib.request.urlopen(base + "/", timeout=5).read().decode()      # test page for the phone browser
        assert "phone sync is working" in page
        phone = linked["phone"]
        body = json.dumps(phone.envelope([phone.bill([dict(p=linked["charger"], q=1, price=40000)],
                                                     {"CASH": 40000})])).encode()
        req = urllib.request.Request(base + "/sdj/sync", data=body, headers={"Content-Type": "application/json"})
        ans = phone.read(json.loads(urllib.request.urlopen(req, timeout=20).read()))
        assert ans["results"][0]["st"] == "DONE"
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        u.settimeout(3)
        u.sendto(b"SDJ?", ("127.0.0.1", srv.udp_port))
        data, _ = u.recvfrom(2048)
        u.close()
        assert data.startswith(b"SDJ!") and json.loads(data[4:])["port"] == phonesync.PORT
    finally:
        srv.stop()


def test_firewall_helper_builds_the_allow_script():
    from sdj.core import winfirewall
    s = winfirewall.script(r"C:\Program Files\SDJ Mobile Care\SDJMobileCare.exe")
    assert "-Action Allow -Program $p -Profile Any" in s
    assert "-Protocol TCP -LocalPort 47810" in s and "-Protocol UDP -LocalPort 47811" in s
    assert "Remove-NetFirewallRule" in s and "C:\\Program Files\\SDJ Mobile Care\\SDJMobileCare.exe" in s
    if not winfirewall.supported():
        assert winfirewall.rule_present() is None
        assert winfirewall.allow()[0] is False


@pytest.mark.skipif(not (sys.platform.startswith("win") and os.environ.get("CI")), reason="Windows CI runner only")
def test_firewall_rules_are_really_created_on_windows():
    """GitHub's Windows runner is an admin: the same script the button runs must create working rules."""
    import subprocess
    from sdj.core import winfirewall
    enc = base64.b64encode(winfirewall.script().encode("utf-16-le")).decode()
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc],
                       capture_output=True, text=True, timeout=180)
    try:
        assert winfirewall.rule_present() is True, r.stdout + r.stderr
        ok, msg = winfirewall.allow(timeout_s=90)          # the button itself (no prompt on the runner)
        assert isinstance(ok, bool) and msg
    finally:
        subprocess.run(["powershell", "-NoProfile", "-Command", f"Remove-NetFirewallRule -DisplayName '{winfirewall.RULE}*'"],
                       capture_output=True, timeout=120)
