import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def make_imei(n: int) -> str:
    """Valid 15-digit IMEI (Luhn check digit) for test data."""
    base = f"35{n:012d}"
    total = 0
    for i, ch in enumerate(reversed(base)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return base + str((10 - total % 10) % 10)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("SDJ_DATA_DIR", str(tmp_path / "sdjdata"))
    from sdj import config
    config._overrides.clear()
    from sdj.core import auth, settings
    from sdj.db.base import dispose_engine
    from sdj.db.init_db import open_database
    open_database(config.db_path())
    settings.clear_cache()
    auth.create_user("admin", "admin123", "ADMIN", "Shop Owner", check_perm=False)
    auth.set_current_user(auth.authenticate("admin", "admin123"))
    yield tmp_path
    dispose_engine()
    auth.set_current_user(None)
    settings.clear_cache()


@pytest.fixture
def cat():
    from sdj.core.inventory import list_categories
    cats = {c["name"]: c["id"] for c in list_categories()}
    return cats


@pytest.fixture
def make_product(db, cat):
    from sdj.core.inventory import create_product

    def _make(name, category, cost, price, qty=0, imeis=None, **extra):
        data = dict(name=name, category_id=cat[category], purchase_price=cost, selling_price=price, **extra)
        units = [{"imei1": i} for i in imeis] if imeis else None
        return create_product(data, opening_qty=qty, opening_cost=cost, imei_units=units)
    return _make


# Qt can crash while Python shuts down AFTER every test passed (seen on the Windows runner: all tests green, exit code 1).
# Cause: the QApplication and its windows are still alive when the interpreter tears everything down. So when the
# test session ends, close the windows and delete the QApplication in the proper order, while Python is still healthy.
def _shutdown_qt() -> None:
    try:
        import shiboken6
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            return
        for w in app.topLevelWidgets():
            w.close()
            w.deleteLater()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()
        shiboken6.delete(app)
    except Exception:           # never turn a green run red because of the tidy-up itself
        pass


def pytest_sessionfinish(session, exitstatus):
    import gc
    _shutdown_qt()
    gc.collect()
