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


# On the CI runners Qt sometimes crashes while Python shuts down AFTER every test has passed, which turned a green run
# red (exit code 1). The result is already known then, so on CI the process ends with it right after pytest's summary.
_final = {"code": None}


def pytest_sessionfinish(session, exitstatus):
    _final["code"] = int(exitstatus)


def pytest_unconfigure(config):
    if os.environ.get("CI") and _final["code"] is not None:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(_final["code"])
