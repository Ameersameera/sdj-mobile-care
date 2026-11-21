"""v1.5.2: live sync ("tell me when something changes"), the PC-started announce, automatic off-PC backups,
the newest backup of each day, background (tray) mode helpers and update notices from the tray."""
import json
import socket
import threading
import time
import urllib.request
from datetime import datetime, timedelta

import pytest

from sdj.core import autobackup, closing, offsite, parties, phonesync, settings
from sdj.core.phonesync import SyncError, seal, unseal
from test_offsite import BackupPhone


@pytest.fixture(autouse=True)
def _service_not_stopping():
    """An earlier test may have stopped the sync service (which answers all waiting phones); start clean."""
    phonesync.wake_waiters(False)
    yield
    phonesync.wake_waiters(False)


def _linked_phone():
    phone = BackupPhone()
    phone.pair(phonesync.new_link_code(1)["code"], name="Owner phone")
    phone.sync(full=True)
    return phone


def _wait_env(phone, token, wait=0.5, aad=b"|wait"):
    env = seal(phone.key, {"token": token, "wait": wait}, phone.device.encode() + aad)
    env["d"] = phone.device
    return env


def _wait(phone, token, wait=0.5):
    return unseal(phone.key, phonesync.handle_wait(_wait_env(phone, token, wait), "127.0.0.1"),
                  phone.device.encode() + b"|wait")


# ================================================================ live sync
def test_wait_answers_quiet_when_nothing_changed(db):
    phone = _linked_phone()
    t0 = time.monotonic()
    ans = _wait(phone, phone.token, wait=0.6)
    assert ans["changed"] is False and ans["token"] == phone.token
    assert 0.5 <= time.monotonic() - t0 < 3


def test_wait_answers_at_once_when_the_phone_is_behind(db):
    phone = _linked_phone()
    t0 = time.monotonic()
    assert _wait(phone, "an-old-token", wait=20)["changed"] is True
    assert time.monotonic() - t0 < 2


def test_wait_answers_as_soon_as_the_pc_data_changes(db):
    phone = _linked_phone()
    token = phone.token

    def later():
        time.sleep(0.4)
        parties.create_customer(dict(name="Meena", phone="9000000077"))
    threading.Thread(target=later).start()
    t0 = time.monotonic()
    ans = _wait(phone, token, wait=20)
    assert ans["changed"] is True and ans["token"] != token
    assert time.monotonic() - t0 < 5                      # not the full 20 seconds


def test_wait_needs_a_linked_phone_and_its_own_purpose(db):
    phone = _linked_phone()
    with pytest.raises(Exception):
        phonesync.handle_wait(_wait_env(phone, phone.token, aad=b"|apk"), "127.0.0.1")
    bad = _wait_env(phone, phone.token)
    bad["d"] = "not-a-phone"
    with pytest.raises(SyncError):
        phonesync.handle_wait(bad, "127.0.0.1")


def test_stopping_the_service_answers_waiting_phones(db):
    phone = _linked_phone()
    out = {}

    def waiter():
        out["ans"] = _wait(phone, phone.token, wait=20)
    th = threading.Thread(target=waiter)
    th.start()
    time.sleep(0.3)
    try:
        phonesync.wake_waiters(True)
        th.join(5)
        assert not th.is_alive() and out["ans"]["changed"] is False
    finally:
        phonesync.wake_waiters(False)


def test_wait_over_http_and_the_pc_started_announce(db):
    from sdj.core.syncserver import SyncServer
    phone = _linked_phone()                                   # its address (127.0.0.1) is known to the PC now
    listen = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listen.bind(("127.0.0.1", 0))
    listen.settimeout(5)
    srv = SyncServer(port=0, udp_port=0, host="127.0.0.1", announce_port=listen.getsockname()[1])
    assert srv.start()
    try:
        data, _ = listen.recvfrom(512)                        # "the PC just started" reaches the phone's address
        assert data == b"SDJ+" + phonesync.pc_identity().encode()
        body = json.dumps(_wait_env(phone, phone.token, wait=0.3)).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{srv.port}/sdj/wait", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            ans = unseal(phone.key, json.loads(r.read()), phone.device.encode() + b"|wait")
        assert ans["changed"] is False
    finally:
        srv.stop()
        listen.close()


# ================================================================ automatic off-PC backups
def test_automatic_backup_only_when_something_changed(db, make_product):
    make_product("Charger 20W", "Chargers", 250, 400, qty=10)
    assert autobackup.due("open")                                   # never made one
    first = autobackup.run("open")
    assert first and first["name"].endswith("_auto.sdjb")
    assert not autobackup.due("open") and not autobackup.due("timer")   # nothing changed since
    parties.create_customer(dict(name="Meena", phone="9000000077"))
    assert not autobackup.due("timer")                              # changed, but made less than 2 hours ago
    settings.set_value("offsite_last_bundle", (datetime.now() - timedelta(hours=3)).isoformat())
    assert autobackup.due("timer")
    second = autobackup.run("timer")
    assert second and second["name"] != first["name"]
    assert offsite.check_bundle(offsite.outbox_dir() / second["name"], "admin123")["ok"]
    parties.create_customer(dict(name="Kavi", phone="9000000078"))
    settings.set_value("offsite_last_bundle", (datetime.now() - timedelta(minutes=20)).isoformat())
    assert autobackup.due("quit") and not autobackup.due("timer")
    assert autobackup.run("quit")["name"].endswith("_exit.sdjb")
    parties.create_customer(dict(name="Selvi", phone="9000000079"))
    settings.set_many({"offsite_last_bundle": (datetime.now() - timedelta(hours=5)).isoformat(), "auto_offsite": False})
    assert not autobackup.due("timer") and autobackup.run("timer") is None      # switched off


def test_close_day_backup_counts_so_no_extra_automatic_one(db, make_product):
    make_product("Charger 20W", "Chargers", 250, 400, qty=10)
    from decimal import Decimal
    from datetime import date
    closing.close_day(date.today(), Decimal("0"))
    settings.set_value("offsite_last_bundle", (datetime.now() - timedelta(hours=3)).isoformat())
    assert not autobackup.due("timer")


def test_the_phone_gets_the_newest_automatic_backup(db, make_product):
    make_product("Charger 20W", "Chargers", 250, 400, qty=10)
    phone = _linked_phone()
    info = autobackup.run("open")
    backups = phone.sync()["backups"]
    assert [b["name"] for b in backups if b["kind"] == "BACKUP"] == [info["name"]]


def test_phones_and_2nd_folder_keep_the_newest_backup_of_each_day():
    names = [f"SDJ_S_2026-11-{d:02d}_{h:02d}0000_auto.sdjb" for d in range(1, 11) for h in (9, 13, 21)]
    keep = offsite.keep_names(names, 5)
    assert keep == {f"SDJ_S_2026-11-{d:02d}_210000_auto.sdjb" for d in range(6, 11)}
    old = ["SDJ_S_2026-10-31_210000_closing.sdjb", "SDJ_S_2026-10-30_210000_closing.sdjb"]
    assert "SDJ_S_2026-10-31_210000_closing.sdjb" in offsite.keep_names(names + old, 5)    # newest of the month


def test_content_mark_changes_with_shop_data(db, make_product):
    a = offsite.content_mark()
    assert a == offsite.content_mark()
    make_product("Charger 20W", "Chargers", 250, 400, qty=10)
    b = offsite.content_mark()
    assert b != a
    settings.set_value("update_last_check", datetime.now().isoformat())     # the program's own notes do not count
    assert offsite.content_mark() == b


# ================================================================ background mode helpers
def test_windows_start_opens_in_the_background():
    from sdj.ui.phone_link import autostart_command
    assert autostart_command().endswith(" --background")


def test_background_argument_is_accepted():
    import argparse
    from sdj.ui import app
    src = open(app.__file__, encoding="utf-8").read()
    assert '"--background"' in src and "_show_running_copy" in src
    assert app._instance_name() == app._instance_name() and app._instance_name().startswith("SDJMobileCare-")
