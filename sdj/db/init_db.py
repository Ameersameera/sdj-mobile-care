"""Create schema, protective triggers and seed data."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from sqlalchemy import select, text

from .. import DB_MARKER
from .base import Base, get_engine, init_engine, session_scope
from .models import Category, Counter, Customer, Setting

SCHEMA_VERSION = 1

CATEGORY_SEED = [
    # (name, group, requires_imei)
    ("Smartphones", "MOBILE", True),
    ("Feature Phones", "MOBILE", True),
    ("Refurbished Phones", "MOBILE", True),
    ("Second-hand Phones", "MOBILE", True),
    ("Chargers", "ACCESSORY", False),
    ("USB Cables", "ACCESSORY", False),
    ("Earphones", "ACCESSORY", False),
    ("Headphones", "ACCESSORY", False),
    ("Bluetooth Speakers", "ACCESSORY", False),
    ("Smartwatches", "ACCESSORY", False),
    ("Mobile Covers", "ACCESSORY", False),
    ("Tempered Glass", "ACCESSORY", False),
    ("Power Banks", "ACCESSORY", False),
    ("Memory Cards", "ACCESSORY", False),
    ("Adapters", "ACCESSORY", False),
    ("Other Accessories", "ACCESSORY", False),
    ("Displays", "SPARE", False),
    ("Batteries", "SPARE", False),
    ("Charging Ports", "SPARE", False),
    ("Speakers", "SPARE", False),
    ("Microphones", "SPARE", False),
    ("Camera Modules", "SPARE", False),
    ("Flex Cables", "SPARE", False),
    ("IC Components", "SPARE", False),
    ("Connectors", "SPARE", False),
    ("Other Repair Components", "SPARE", False),
]

PROTECTED_TABLES = ["sales", "sale_items", "payments", "purchases", "purchase_items", "stock_movements",
                    "returns", "return_items", "expenses", "service_jobs", "service_items",
                    "daily_closings", "audit_logs", "warranty_claims"]

TRIGGERS = [
    *[f"""CREATE TRIGGER IF NOT EXISTS trg_nodelete_{t} BEFORE DELETE ON {t}
          BEGIN SELECT RAISE(ABORT, 'Financial records cannot be deleted. Cancel or void them instead.'); END;"""
      for t in PROTECTED_TABLES],
    """CREATE TRIGGER IF NOT EXISTS trg_noupdate_stock_movements BEFORE UPDATE ON stock_movements
       BEGIN SELECT RAISE(ABORT, 'Stock history cannot be edited.'); END;""",
    """CREATE TRIGGER IF NOT EXISTS trg_noupdate_audit_logs BEFORE UPDATE ON audit_logs
       BEGIN SELECT RAISE(ABORT, 'The audit log is read-only.'); END;""",
    """CREATE TRIGGER IF NOT EXISTS trg_locked_closing BEFORE UPDATE ON daily_closings
       WHEN OLD.is_locked = 1 AND NEW.is_locked = 1
       BEGIN SELECT RAISE(ABORT, 'This day is closed and locked. An admin must reopen it first.'); END;""",
]


def _auto_migrate(conn) -> list[str]:
    """Add columns introduced by newer versions to an existing database (never drops anything)."""
    added = []
    for table in Base.metadata.tables.values():
        existing = {row[1] for row in conn.execute(text(f'PRAGMA table_info("{table.name}")'))}
        if not existing:
            continue
        for col in table.columns:
            if col.name in existing:
                continue
            impl = getattr(col.type, "impl", col.type)
            type_sql = impl.compile(dialect=conn.dialect) if not isinstance(impl, type) else "INTEGER"
            default = col.default.arg if col.default is not None and not callable(col.default.arg) else None
            if default is None and not col.nullable:
                default = 0 if "INT" in type_sql.upper() or "BOOL" in type_sql.upper() else ""
            if hasattr(col.type, "process_bind_param") and default is not None and default != "":
                default = col.type.process_bind_param(default, conn.dialect)
            ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {type_sql}'
            if default is not None:
                ddl += f" NOT NULL DEFAULT {default!r}" if not col.nullable else f" DEFAULT {default!r}"
            conn.execute(text(ddl))
            added.append(f"{table.name}.{col.name}")
    return added


def create_schema() -> None:
    engine = get_engine()
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        added = _auto_migrate(conn)
        if added:
            import logging
            logging.getLogger(__name__).info("Database upgraded, new columns: %s", ", ".join(added))
        for ddl in TRIGGERS:
            conn.execute(text(ddl))


def seed() -> None:
    from ..core.settings import DEFAULTS, clear_cache
    with session_scope() as s:
        existing = {c.name for c in s.scalars(select(Category))}
        for i, (name, grp, imei) in enumerate(CATEGORY_SEED):
            if name not in existing:
                s.add(Category(name=name, group=grp, requires_imei=imei, sort_order=i))
        if s.scalar(select(Customer).where(Customer.is_walk_in.is_(True))) is None:
            s.add(Customer(name="Walk-in Customer", phone="", is_walk_in=True))
        keys = {k for k in s.scalars(select(Setting.key))}
        for k, v in DEFAULTS.items():
            if k not in keys:
                s.add(Setting(key=k, value=json.dumps(v)))
        for k, v in (("db_marker", DB_MARKER), ("schema_version", SCHEMA_VERSION)):
            if k not in keys:
                s.add(Setting(key=k, value=json.dumps(v)))
        for name in ("product_code", "job_no", "purchase_no", "claim_no"):
            if s.get(Counter, name) is None:
                s.add(Counter(name=name, value=0))
    clear_cache()


def open_database(path: str | Path) -> None:
    """Open (creating if needed) the database and make sure the schema is current."""
    init_engine(path)
    create_schema()
    seed()


def quick_check(path: str | Path) -> tuple[bool, str]:
    """Low-level integrity check that does not need SQLAlchemy (used for crash recovery)."""
    path = Path(path)
    if not path.exists():
        return True, "new database"
    try:
        con = sqlite3.connect(str(path))
        try:
            row = con.execute("PRAGMA quick_check").fetchone()
            ok = bool(row) and row[0] == "ok"
            return ok, (row[0] if row else "no result")
        finally:
            con.close()
    except sqlite3.DatabaseError as exc:
        return False, str(exc)


def is_sdj_database(path: str | Path) -> tuple[bool, str]:
    """Verify that *path* is a healthy SDJ Mobile Care database (used before restore)."""
    ok, msg = quick_check(path)
    if not ok:
        return False, f"Integrity check failed: {msg}"
    try:
        con = sqlite3.connect(str(path))
        try:
            row = con.execute("SELECT value FROM settings WHERE key='db_marker'").fetchone()
        finally:
            con.close()
    except sqlite3.DatabaseError as exc:
        return False, f"Not a valid database: {exc}"
    if not row or json.loads(row[0]) != DB_MARKER:
        return False, "This file is not an SDJ Mobile Care database."
    return True, "ok"
