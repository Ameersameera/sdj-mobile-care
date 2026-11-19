"""Automatic / manual backups and restore (uses SQLite's online backup API – safe while running)."""
from __future__ import annotations

import logging
import os
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from .. import config
from ..db import base
from ..db.init_db import is_sdj_database, open_database, quick_check
from . import settings
from .errors import BusinessError

log = logging.getLogger(__name__)


def backups_dir() -> Path:
    try:
        custom = (settings.get("backup_dir") or "").strip()
    except Exception:  # database unavailable (e.g. during crash recovery)
        custom = ""
    p = Path(custom) if custom else config.default_backups_dir()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _db_file() -> Path:
    return base.current_db_path() or config.db_path()


def backup_now(label: str = "manual", dest_dir: str | Path | None = None, dest_file: str | Path | None = None) -> Path:
    src_path = _db_file()
    if not src_path.exists():
        raise BusinessError("Database file not found.")
    if dest_file:
        target = Path(dest_file)
        target.parent.mkdir(parents=True, exist_ok=True)
    else:
        folder = Path(dest_dir) if dest_dir else backups_dir()
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"SDJ_backup_{datetime.now():%Y%m%d_%H%M%S}_{label}.db"
    tmp = target.with_suffix(".tmp")
    src = sqlite3.connect(str(src_path), timeout=15)
    dst = sqlite3.connect(str(tmp))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    ok, msg = quick_check(tmp)
    if not ok:
        tmp.unlink(missing_ok=True)
        raise BusinessError(f"Backup verification failed: {msg}")
    os.replace(tmp, target)
    if not dest_file:
        try:
            settings.set_value("last_backup_at", datetime.now().replace(microsecond=0).isoformat())
        except Exception:  # pragma: no cover - never let bookkeeping break a backup
            log.exception("could not store last backup time")
        cleanup_old_backups()
    log.info("Backup written: %s", target)
    return target


def list_backups() -> list[dict]:
    out = []
    for p in sorted(backups_dir().glob("SDJ_backup_*.db"), key=lambda x: x.stat().st_mtime, reverse=True):
        st = p.stat()
        out.append(dict(path=str(p), name=p.name, size_kb=round(st.st_size / 1024, 1),
                        modified=datetime.fromtimestamp(st.st_mtime).replace(microsecond=0)))
    return out


def cleanup_old_backups(keep: int | None = None) -> int:
    keep = int(keep or settings.get("backup_keep") or 30)
    files = sorted(backups_dir().glob("SDJ_backup_*.db"), key=lambda x: x.stat().st_mtime, reverse=True)
    removed = 0
    for p in files[keep:]:
        if "_pre_restore" in p.name or "_before_reset" in p.name:
            continue
        try:
            p.unlink()
            removed += 1
        except OSError:
            log.warning("could not delete old backup %s", p)
    return removed


def auto_backup_if_due(max_age_hours: float = 20) -> Path | None:
    if not settings.get("auto_backup"):
        return None
    last = settings.get("last_backup_at") or ""
    try:
        last_dt = datetime.fromisoformat(last) if last else None
    except ValueError:
        last_dt = None
    if last_dt and datetime.now() - last_dt < timedelta(hours=max_age_hours):
        return None
    try:
        return backup_now("auto")
    except Exception:
        log.exception("automatic backup failed")
        return None


def restore_backup(src: str | Path) -> Path:
    """Replace the live database with *src*. A safety copy of the current data is made first."""
    src = Path(src)
    ok, msg = is_sdj_database(src)
    if not ok:
        raise BusinessError(msg)
    live = _db_file()
    safety = None
    if live.exists():
        try:
            safety = backup_now("pre_restore")
        except Exception as exc:  # the current file may be the corrupt one – keep a raw copy instead
            log.warning("safety backup failed (%s); keeping raw copy", exc)
            safety = backups_dir() / f"SDJ_backup_{datetime.now():%Y%m%d_%H%M%S}_raw_pre_restore.db"
            shutil.copy2(live, safety)
    with base.db_lock:                       # no phone sync may write while the file is swapped
        base.dispose_engine()
        for suffix in ("-wal", "-shm", "-journal"):
            Path(str(live) + suffix).unlink(missing_ok=True)
        tmp = live.with_suffix(".restoring")
        shutil.copy2(src, tmp)
        os.replace(tmp, live)
        open_database(live)
        settings.clear_cache()
    log.info("Database restored from %s (safety copy: %s)", src, safety)
    return safety or live


def latest_good_backup() -> Path | None:
    for b in list_backups():
        p = Path(b["path"])
        if is_sdj_database(p)[0]:
            return p
    return None
