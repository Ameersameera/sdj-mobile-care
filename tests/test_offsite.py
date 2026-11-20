"""Off-PC backups (v1.4): encrypted bundle after closing, phones keep it, 2nd folder, recovery code,
restore to a new PC from a phone or a file, and the emergency rebuild from a phone's copy."""
import base64
import hashlib
import json
import sqlite3
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from conftest import make_imei
from test_phone_sync import FakePhone
from sdj import config
from sdj.core import auth, closing, offsite, parties, phonesync, repairs, sales, settings
from sdj.core.errors import BusinessError, ValidationError
from sdj.core.phonesync import pair_key, seal, unseal


class BackupPhone(FakePhone):
    """FakePhone that also acknowledges and downloads backups, like the Android app."""

    def __init__(self):
        super().__init__()
        self.epoch = ""
        self.backups = []

    def envelope(self, ops=(), full=False, seq=None, acks=()):
        self.seq = seq if seq is not None else self.seq + 1
        req = {"seq": self.seq, "ops": list(ops), "token": self.token, "hashes": self.hashes, "full": full,
               "app": "t", "acks": list(acks)}
        env = seal(self.key, req, self.device.encode())
        env["d"] = self.device
        return env

    def read(self, ans):
        out = super().read(ans)
        self.epoch = out.get("epoch", "")
        self.backups = out.get("backups", [])
        return out

    def sync(self, ops=(), full=False, seq=None, acks=()):
        return self.read(phonesync.handle_sync(self.envelope(ops, full, seq, acks), "127.0.0.1"))

    def backup_request(self, item_id):
        self.seq += 1
        env = seal(self.key, {"seq": self.seq, "id": item_id, "app": "t"}, self.device.encode() + b"|backup")
        env["d"] = self.device
        return env

    def copy(self):
        return dict(data=self.data, device=dict(uid=self.device, key=base64.b64encode(self.key).decode(), no=self.no,
                                                name="Owner phone", user_id=self.user["id"]),
                    epoch=self.epoch, made=datetime.now().isoformat(), hello=uuid.uuid4().hex)


@pytest.fixture
def shop(db, make_product):
    """An 'old PC' with stock, a credit bill, an open repair with advance, a finished and a delivered job."""
    charger = make_product("Charger 20W", "Chargers", 250, 400, qty=10)
    mobile = make_product("Vivo Y28", "Smartphones", 12000, 14000, qty=1, imeis=[make_imei(7)])
    display = make_product("Display Redmi 9", "Displays", 900, 1500, qty=5)
    ravi = parties.create_customer(dict(name="Ravi Kumar", phone="9000000002", opening_balance=500))
    sale = sales.create_sale([sales.CartItem(product_id=charger, quantity=1, unit_price=Decimal("400"))],
                             customer_id=ravi, payments={"CASH": Decimal("100")})
    open_job = repairs.create_job(dict(customer_name="Ravi Kumar", customer_phone="9000000002", brand="Redmi",
                                       model="9", problem="Display broken", labour_charge=300),
                                  advance=200)["id"]
    repairs.add_part(open_job, display, 1)
    ready_job = repairs.create_job(dict(customer_name="Kumar", customer_phone="9000000003", brand="Samsung",
                                        model="M31", problem="Charging", labour_charge=500))["id"]
    repairs.set_status(ready_job, "READY")
    done_job = repairs.create_job(dict(customer_name="Kumar", customer_phone="9000000003", brand="Oppo",
                                       model="A5", problem="Speaker", labour_charge=250))["id"]
    repairs.set_status(done_job, "READY")
    repairs.deliver_job(done_job, collect_amount=250)
    kumar = next(c["id"] for c in parties.list_customers() if c["phone"] == "9000000003")
    phone = BackupPhone()
    phone.pair(phonesync.new_link_code(1)["code"], name="Owner phone")
    phone.sync(full=True)
    return dict(phone=phone, charger=charger, mobile=mobile, display=display, ravi=ravi, kumar=kumar,
                open_job=open_job, ready_job=ready_job, done_job=done_job, sale=sale)


def _new_pc(monkeypatch, tmp_path, name="newpc"):
    """Switch to an empty data folder, as on a freshly installed PC."""
    from sdj.db.base import dispose_engine
    from sdj.db.init_db import open_database
    dispose_engine()
    monkeypatch.setenv("SDJ_DATA_DIR", str(tmp_path / name))
    auth.set_current_user(None)
    open_database(config.db_path())
    settings.clear_cache()
    assert auth.user_count() == 0


def _events():
    got = []
    phonesync.subscribe(lambda e, info: got.append((e, info)))
    return got


def _server():
    from sdj.core.syncserver import SyncServer
    srv = SyncServer(port=0, udp_port=0, host="127.0.0.1")
    assert srv.start()
    return srv


def _post(url, body: bytes, headers=None):
    req = urllib.request.Request(url, data=body, headers=headers or {"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=30)


# ================================================================ the bundle itself
def test_closing_makes_an_encrypted_checked_bundle(shop):
    res = closing.close_day(date.today(), Decimal("0"))
    assert res["offsite"] and res["pdf_path"]
    path = Path(res["offsite"]["path"])
    raw = path.read_bytes()
    assert raw.startswith(offsite.MAGIC) and path.suffix == ".sdjb"
    assert b"Ravi Kumar" not in raw and b"SQLite format" not in raw            # encrypted
    info = offsite.describe_file(path)
    assert info["owners"] == ["admin"] and info["date"] == date.today().isoformat()
    ok = offsite.check_bundle(path, "admin123")
    assert ok["ok"] and ok["counts"]["products"] == 3 and ok["counts"]["service_jobs"] == 3
    with pytest.raises(ValidationError):
        offsite.check_bundle(path, "wrong-password")
    folder = offsite.open_bundle(path, "admin123")
    assert (folder / "closing_report.pdf").read_bytes().startswith(b"%PDF")
    assert json.loads((folder / "manifest.json").read_text())["label"] == "closing"
    # a changed byte anywhere is detected
    for pos in (len(raw) - 5, 30):
        bad = bytearray(raw)
        bad[pos] ^= 0x41
        tampered = path.with_name("tampered.sdjb")
        tampered.write_bytes(bytes(bad))
        with pytest.raises(ValidationError):
            offsite.check_bundle(tampered, "admin123")


def test_recovery_code_opens_backups_when_the_password_is_lost(db):
    with pytest.raises(ValidationError):
        offsite.create_recovery_code("not-my-password")
    code = offsite.create_recovery_code("admin123")
    assert len(code) == 29 and code.count("-") == 5
    first = Path(offsite.create_bundle("manual")["path"])
    assert offsite.describe_file(first)["recovery"]
    assert offsite.check_bundle(first, code)["ok"]
    assert offsite.check_bundle(first, code.lower().replace("-", " "))["ok"]      # spaces / lower case are fine
    auth.change_own_password("admin123", "newpass99")
    second = Path(offsite.create_bundle("manual")["path"])
    assert offsite.check_bundle(second, "newpass99")["ok"] and offsite.check_bundle(second, code)["ok"]
    assert offsite.check_bundle(first, "admin123")["ok"]            # old backups keep the old password
    with pytest.raises(ValidationError):
        offsite.check_bundle(second, "admin123")


def test_restore_from_file_on_a_new_pc_brings_data_and_logo(shop, tmp_path, monkeypatch):
    logo_dir = config.logo_store_dir()
    logo_dir.mkdir(parents=True, exist_ok=True)
    png = config.asset_path("logo.png").read_bytes()
    (logo_dir / "logo_shop.png").write_bytes(png)
    settings.set_value("shop_logo_path", str(logo_dir / "logo_shop.png"))
    offsite.create_recovery_code("admin123")
    bundle = Path(closing.close_day(date.today(), Decimal("0"))["offsite"]["path"])
    pen_drive = tmp_path / "pendrive"
    pen_drive.mkdir()
    copy = pen_drive / bundle.name
    copy.write_bytes(bundle.read_bytes())
    _new_pc(monkeypatch, tmp_path)
    with pytest.raises(ValidationError):
        offsite.restore_any(copy, "bad")
    assert auth.user_count() == 0                                     # nothing changed
    out = offsite.restore_any(copy, "admin123")
    assert out["counts"]["products"] == 3
    assert auth.authenticate("admin", "admin123").role == "ADMIN"
    from sdj.core import inventory
    assert {p["name"] for p in inventory.list_products()} >= {"Charger 20W"}
    logo = Path(settings.get("shop_logo_path"))
    assert logo.exists() and logo.read_bytes() == png
    assert str(logo).startswith(str(tmp_path / "newpc"))             # moved into the new PC's folder
    assert phonesync.list_devices()[0]["name"] == "Owner phone"      # the phone stays linked


def test_second_backup_folder_copy_retention_and_late_warning(db, tmp_path):
    pen = tmp_path / "pendrive" / "SDJ Backups"
    offsite.set_second_folder(str(pen))
    b = offsite.create_bundle("manual")
    assert (pen / b["name"]).exists()
    st = offsite.status()
    assert st["second_folder"] == str(pen) and st["second_last"] is not None and not st["late"]
    # pen drive removed: the copy fails quietly and is caught up when it is back
    moved = tmp_path / "away"
    pen.parent.rename(moved)
    b2 = offsite.create_bundle("manual")
    assert offsite.status()["second_error"]
    moved.rename(pen.parent)
    offsite.catch_up_second_folder()
    assert (pen / b2["name"]).exists() and not offsite.status()["second_error"]
    settings.set_value("offsite_second_last", (datetime.now() - timedelta(days=8)).isoformat())
    st = offsite.status()
    assert st["late"] and any("2nd backup folder" in w for w in st["warnings"])


def test_retention_keeps_30_days_and_one_per_month():
    start = date(2026, 1, 1)
    names = [f"SDJ_Shop_{start + timedelta(days=i):%Y-%m-%d}_210000_closing.sdjb" for i in range(120)]
    keep = offsite.keep_names(names, 30)
    newest = sorted(names)[-30:]
    assert set(newest) <= keep
    months = {"2026-01-31", "2026-02-28", "2026-03-31"}
    assert {n for n in keep if n not in newest} == {n for n in names if any(m in n for m in months)}


# ================================================================ phones keep the backups
def test_closing_backup_and_report_reach_the_owner_phone(shop):
    phone = shop["phone"]
    assert phone.sync()["backups"] == []                              # nothing made yet
    closing.close_day(date.today(), Decimal("0"))
    ans = phone.sync()
    kinds = sorted(b["kind"] for b in ans["backups"])
    assert kinds == ["BACKUP", "REPORT"]
    got = {}
    for b in ans["backups"]:
        item = phonesync.handle_backup(phone.backup_request(b["id"]), "127.0.0.1")
        data = Path(item["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == b["sha"] == item["sha"]
        got[b["kind"]] = data
    assert got["BACKUP"].startswith(offsite.MAGIC) and got["REPORT"].startswith(b"%PDF")
    ans = phone.sync(acks=[b["sha"] for b in ans["backups"]])
    assert ans["backups"] == []
    st = offsite.status()
    assert st["phone_last"] is not None and not st["late"]
    # the next day's closing sends only the new ones
    settings.set_value("offsite_last_error", "")
    offsite.create_bundle("manual")
    assert [b["kind"] for b in phone.sync()["backups"]] == ["BACKUP"]


def test_staff_phone_keeps_backups_only_when_turned_on(shop):
    closing.close_day(date.today(), Decimal("0"))
    uid = auth.create_user("kavin", "staff123", "CASHIER", "Kavin")
    staff = BackupPhone()
    staff.pair(phonesync.new_link_code(uid)["code"], name="Staff phone")
    assert staff.sync(full=True)["backups"] == []
    with pytest.raises(phonesync.SyncError):
        phonesync.handle_backup(staff.backup_request(1), "127.0.0.1")
    dev = next(d for d in phonesync.list_devices() if d["name"] == "Staff phone")
    offsite.set_device_backups(dev["id"], True)
    assert len(staff.sync()["backups"]) == 2


def test_backup_download_over_the_network(shop):
    closing.close_day(date.today(), Decimal("0"))
    phone = shop["phone"]
    item = next(b for b in phone.sync()["backups"] if b["kind"] == "BACKUP")
    srv = _server()
    try:
        r = _post(f"http://127.0.0.1:{srv.port}/sdj/backup", json.dumps(phone.backup_request(item["id"])).encode())
        data = r.read()
        assert r.headers["X-SDJ-Sha256"] == item["sha"] == hashlib.sha256(data).hexdigest()
        env = phone.backup_request(item["id"])
        assert _post(f"http://127.0.0.1:{srv.port}/sdj/backup", json.dumps(env).encode()).read() == data
        with pytest.raises(urllib.error.HTTPError) as err:          # the same (replayed) request is refused
            _post(f"http://127.0.0.1:{srv.port}/sdj/backup", json.dumps(env).encode()).read()
        assert err.value.code == 409
    finally:
        srv.stop()


# ================================================================ restore a new PC from the phone
def test_new_pc_restores_the_backup_sent_from_the_phone(shop, tmp_path, monkeypatch):
    phone = shop["phone"]
    closing.close_day(date.today(), Decimal("0"))
    offered = phone.sync()["backups"]
    item = next(b for b in offered if b["kind"] == "BACKUP")
    blob = Path(phonesync.handle_backup(phone.backup_request(item["id"]), "127.0.0.1")["path"]).read_bytes()
    saved_shas = [b["sha"] for b in offered]
    assert phone.sync(acks=saved_shas)["backups"] == []
    old_epoch, old_pc = phone.epoch, phonesync.pc_identity()
    late_op = phone.bill([dict(p=shop["charger"], q=1, price=40000)], {"CASH": 40000})
    assert phone.sync([late_op])["results"][0]["st"] == "DONE"        # saved on the old PC after the backup
    _new_pc(monkeypatch, tmp_path)
    events = _events()
    info = phonesync.new_restore_code()                               # allowed: nobody exists on a new PC yet
    assert info["qr"].startswith("SDJRESTORE:1;") and phonesync.sync_wanted()
    srv = _server()
    try:
        url = f"http://127.0.0.1:{srv.port}/sdj/restore"
        meta = dict(name=item["name"], size=len(blob), sha=hashlib.sha256(blob).hexdigest(), phone="Owner phone",
                    hello="h1")
        pc_new = phonesync.pc_identity()
        bad = seal(pair_key("WRONGCODE2", pc_new), meta, b"restore")
        with pytest.raises(urllib.error.HTTPError) as err:
            _post(url, blob, {"X-SDJ-Auth": json.dumps(bad), "Content-Type": "application/octet-stream"})
        assert err.value.code == 403
        good = seal(pair_key(info["code"], pc_new), meta, b"restore")
        good["pc"] = pc_new
        ans = unseal(pair_key(info["code"], pc_new),
                     json.loads(_post(url, blob, {"X-SDJ-Auth": json.dumps(good),
                                                  "Content-Type": "application/octet-stream"}).read()), b"restore")
        assert ans["ok"] and ans["hello"] == "h1"
        broken = seal(pair_key(info["code"], pc_new), dict(meta, sha="0" * 64), b"restore")
        with pytest.raises(urllib.error.HTTPError) as err:          # damaged on the way → not accepted
            _post(url, blob, {"X-SDJ-Auth": json.dumps(broken), "Content-Type": "application/octet-stream"})
        assert err.value.code == 400
    finally:
        srv.stop()
    got = [i for e, i in events if e == "restore_file"]
    assert len(got) == 1 and got[0]["info"]["kind"] == "bundle" and got[0]["info"]["shop"]
    offsite.restore_any(got[0]["path"], "admin123")
    phonesync.cancel_restore_code()
    assert phonesync.pc_identity() == old_pc                           # phones find this PC as before
    assert auth.authenticate("admin", "admin123")
    ans = phone.sync()                                                 # same phone, same key: still linked
    assert ans["epoch"] and ans["epoch"] != old_epoch                  # tells the phone to re-send recent entries
    again = phone.sync([late_op])["results"][0]                        # the entry made after the backup comes back
    assert again["st"] == "DONE"
    assert any(s["invoice_no"] == again["res"]["no"] for s in sales.list_sales())
    # the new PC's own next backup reaches the phone, although its numbering started again from the restored data
    auth.set_current_user(auth.authenticate("admin", "admin123"))
    new_bundle = offsite.create_bundle("manual")
    offered = phone.sync(acks=saved_shas)["backups"]
    assert [b["name"] for b in offered if b["kind"] == "BACKUP"] == [new_bundle["name"]]


def test_rebuild_needs_a_waiting_restore_code(db):
    phone_like = dict(data={}, hello="x")
    env = seal(pair_key("ABCDEFGHJK", phonesync.pc_identity()), phone_like, b"rebuild")
    with pytest.raises(phonesync.SyncError):
        phonesync.handle_rebuild(env, "127.0.0.1")


# ================================================================ emergency: no backup, rebuild from the phone
def test_emergency_rebuild_from_the_phone_copy(shop, tmp_path, monkeypatch):
    phone = shop["phone"]
    old_bal = {c: parties.customer_balance(shop[c]) for c in ("ravi", "kumar")}
    assert old_bal == {"ravi": Decimal("600.00"), "kumar": Decimal("500.00")}
    old_open = repairs.get_job(shop["open_job"])
    old_invoice = sales.list_sales()[0]["invoice_no"]
    payload = phone.copy()
    _new_pc(monkeypatch, tmp_path)
    events = _events()
    info = phonesync.new_restore_code()
    srv = _server()
    try:
        pc_new = phonesync.pc_identity()
        env = seal(pair_key(info["code"], pc_new), payload, b"rebuild")
        env["pc"] = pc_new
        r = json.loads(_post(f"http://127.0.0.1:{srv.port}/sdj/rebuild", json.dumps(env).encode()).read())
        ans = unseal(pair_key(info["code"], pc_new), r, b"rebuild")
        assert ans["ok"] and ans["summary"]["products"] == 3 and ans["summary"]["open_jobs"] == 2
    finally:
        srv.stop()
    got = [i for e, i in events if e == "rebuild"]
    assert len(got) == 1
    req = json.loads(Path(got[0]["path"]).read_text())
    with pytest.raises(ValidationError):
        offsite.rebuild_from_phone(req, "wrong")
    assert auth.user_count() == 0
    summary = offsite.rebuild_from_phone(req, "admin123")
    assert summary["customers"] >= 2
    # same logins, same numbers, same stock
    auth.set_current_user(auth.authenticate("admin", "admin123"))
    from sdj.core import inventory
    prods = {p["id"]: p for p in inventory.list_products()}
    assert prods[shop["charger"]]["quantity"] == 9 and prods[shop["display"]]["quantity"] == 4
    assert prods[shop["mobile"]]["quantity"] == 1 and inventory.find_imei_in_stock(make_imei(7))
    assert {c: parties.customer_balance(shop[c]) for c in ("ravi", "kumar")} == old_bal
    job = repairs.get_job(shop["open_job"])
    assert job["job_no"] == old_open["job_no"] and job["paid"] == old_open["paid"] == Decimal("200.00")
    assert job["total"] == old_open["total"] and len(job["items"]) == 1
    assert repairs.get_job(shop["ready_job"])["status"] == "READY"
    with pytest.raises(BusinessError):
        repairs.get_job(shop["done_job"])
    con = sqlite3.connect(config.db_path())
    try:
        for pid, qty in con.execute("SELECT id, quantity FROM products"):
            total = con.execute("SELECT COALESCE(SUM(qty_change),0) FROM stock_movements WHERE product_id=?",
                                (pid,)).fetchone()[0]
            assert total == qty
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        con.close()
    # numbers continue after the old ones
    new_sale = sales.create_sale([sales.CartItem(product_id=shop["charger"], quantity=1, unit_price=Decimal("400"))],
                                 payments={"CASH": Decimal("400")})
    assert new_sale["invoice_no"] > old_invoice
    new_job = repairs.create_job(dict(customer_name="X", customer_phone="9000000009", brand="A", problem="B"))
    assert new_job["job_no"] > repairs.get_job(shop["ready_job"])["job_no"]
    # the phone that sent the copy is still linked and keeps working
    ans = phone.sync()
    assert ans["epoch"] == payload["epoch"]                    # no re-sending (its entries are already in the copy)
    op = phone.bill([dict(p=shop["charger"], q=1, price=40000)], {"CASH": 40000})
    assert phone.sync([op])["results"][0]["st"] == "DONE"
    with pytest.raises(BusinessError):
        offsite.rebuild_from_phone(req, "admin123")            # only once, on an empty PC
