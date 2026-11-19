"""Engine, session handling and exact money column types.

Money is stored in SQLite as INTEGER paise (exact, no floating point drift)
and exposed to Python as decimal.Decimal rupees.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from pathlib import Path
from typing import Iterator

from sqlalchemy import Integer, create_engine, event, types
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

ZERO = Decimal("0.00")
TWO_PLACES = Decimal("0.01")
FOUR_PLACES = Decimal("0.0001")


def D(value) -> Decimal:
    """Convert anything (str, int, float, Decimal, None) to Decimal safely."""
    if value is None or value == "":
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(repr(value))
    try:
        return Decimal(str(value).replace(",", "").replace("₹", "").strip() or "0")
    except InvalidOperation as exc:  # pragma: no cover - defensive
        raise ValueError(f"Invalid number: {value!r}") from exc


def q2(value) -> Decimal:
    """Round to 2 decimal places (half-up, the way Indian invoices round)."""
    return D(value).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def q4(value) -> Decimal:
    return D(value).quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)


class Money(types.TypeDecorator):
    """Rupees as Decimal in Python, integer paise in the database."""

    impl = Integer
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return int((D(value) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return (Decimal(int(value)) / 100).quantize(TWO_PLACES)


class Cost(types.TypeDecorator):
    """Unit cost with 4 decimals (weighted average cost), stored as integer."""

    impl = Integer
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return int((D(value) * 10000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return (Decimal(int(value)) / 10000).quantize(FOUR_PLACES)


class Base(DeclarativeBase):
    pass


class _DB:
    engine: Engine | None = None
    factory: sessionmaker | None = None
    path: Path | None = None


_db = _DB()

# One unit of work at a time inside this program. The screens and the phone-sync service run in different
# threads; taking this (re-entrant) lock for every session keeps SQLite free of "database is locked" errors.
db_lock = threading.RLock()


def _configure_sqlite(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _record):  # noqa: ANN001
        # Let SQLAlchemy control transactions explicitly (proper atomicity).
        dbapi_conn.isolation_level = None
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=FULL")
        cur.execute("PRAGMA busy_timeout=8000")
        cur.close()

    @event.listens_for(engine, "begin")
    def _on_begin(conn):  # noqa: ANN001
        conn.exec_driver_sql("BEGIN")


def init_engine(path: str | Path) -> Engine:
    """(Re)open the database at *path*."""
    dispose_engine()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path.as_posix()}", future=True)
    _configure_sqlite(engine)
    _db.engine = engine
    _db.factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    _db.path = path
    return engine


_dispose_hooks: list = []


def on_dispose(fn) -> None:
    """Call *fn* before the database is closed (e.g. to close a watcher connection before a restore)."""
    if fn not in _dispose_hooks:
        _dispose_hooks.append(fn)


def dispose_engine() -> None:
    for fn in list(_dispose_hooks):
        try:
            fn()
        except Exception:  # pragma: no cover - a hook must never block closing the database
            pass
    if _db.engine is not None:
        _db.engine.dispose()
    _db.engine = None
    _db.factory = None


def get_engine() -> Engine:
    if _db.engine is None:
        raise RuntimeError("Database is not initialised")
    return _db.engine


def current_db_path() -> Path | None:
    return _db.path


@contextmanager
def session_scope() -> Iterator[Session]:
    """One atomic unit of work: commits on success, rolls back on any error."""
    with db_lock:
        if _db.factory is None:
            raise RuntimeError("Database is not initialised")
        session: Session = _db.factory()
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()
