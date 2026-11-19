"""Backups, restore, corruption detection and crash safety."""
from decimal import Decimal as Dec
from pathlib import Path

import pytest

from sdj import config
from sdj.core import backup, inventory, sales
from sdj.core.errors import BusinessError
from sdj.db.base import dispose_engine
from sdj.db.init_db import is_sdj_database, open_database, quick_check


def test_backup_and_restore_roundtrip(db, make_product):
    pid = make_product("Charger", "Chargers", 250, 400, qty=10)
    b = backup.backup_now("test")
    assert Path(b).exists() and is_sdj_database(b)[0]
    sales.quick_sale(pid, 4, 400, "CASH")
    assert inventory.get_product(pid)["quantity"] == 6
    safety = backup.restore_backup(b)
    assert inventory.get_product(pid)["quantity"] == 10      # back to the backup state
    assert Path(safety).exists()                              # the data before restore is kept too
    assert sales.list_sales() == []


def test_restore_rejects_foreign_files(db, tmp_path):
    bad = tmp_path / "notes.db"
    bad.write_bytes(b"this is not a database")
    with pytest.raises(BusinessError):
        backup.restore_backup(bad)


def test_corruption_detected_and_recovered_from_backup(db, make_product):
    pid = make_product("Earphones", "Earphones", 150, 300, qty=20)
    good = backup.backup_now("auto")
    live = config.db_path()
    dispose_engine()
    for suffix in ("-wal", "-shm"):
        Path(str(live) + suffix).unlink(missing_ok=True)
    data = bytearray(live.read_bytes())
    data[100:4096] = b"\x00" * (4096 - 100)          # destroy the header/schema pages
    data[0:16] = b"CORRUPTED FILE!!"
    live.write_bytes(bytes(data))
    ok, _ = quick_check(live)
    assert not ok
    latest = backup.latest_good_backup()
    assert latest is not None and Path(latest) == Path(good)
    backup.restore_backup(latest)
    assert inventory.get_product(pid)["quantity"] == 20


def test_failed_transaction_leaves_no_partial_data(db, make_product):
    """A sale that fails half-way (2nd line has no stock) must not deduct the 1st line."""
    a = make_product("Cable A", "USB Cables", 50, 120, qty=5)
    b = make_product("Cable B", "USB Cables", 50, 120, qty=0)
    from sdj.core.sales import CartItem
    with pytest.raises(Exception):
        sales.create_sale([CartItem(a, 2, Dec(120)), CartItem(b, 1, Dec(120))], payments={"CASH": 360})
    assert inventory.get_product(a)["quantity"] == 5
    assert sales.list_sales() == []


def test_auto_backup_cleanup(db):
    from sdj.core import settings
    settings.set_value("backup_keep", 3)
    for i in range(5):
        backup.backup_now(f"t{i}")
        import time
        time.sleep(1.05)
    assert len(backup.list_backups()) == 3
