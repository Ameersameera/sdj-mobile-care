"""Automatic off-PC backups (v1.5.2).

Besides the Close Day backup, a locked backup is made by itself:
* when the program opens (if something changed and the newest backup is over an hour old),
* every 2 hours while it runs – also in the background (tray) – only when something changed,
* when it closes, if something changed in the last part of the day.
Each one goes to the 2nd backup folder and reaches the owner phones at their next sync, exactly like the Close Day
backup. Phones and the 2nd folder keep the newest backup of each day, so space is not wasted.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

from sqlalchemy import func, select

from ..db.base import session_scope
from ..db.models import User
from . import offsite, settings

log = logging.getLogger(__name__)

MIN_AGE = {"open": timedelta(hours=1), "timer": timedelta(hours=2), "quit": timedelta(minutes=15)}
LABEL = {"open": "auto", "timer": "auto", "quit": "exit"}
_lock = threading.Lock()


def enabled() -> bool:
    return bool(settings.get("auto_offsite"))


def _last_bundle() -> datetime | None:
    v = settings.get("offsite_last_bundle") or ""
    try:
        return datetime.fromisoformat(v) if v else None
    except ValueError:
        return None


def _shop_ready() -> bool:
    """Only for a shop that is set up (an owner exists); a brand-new PC has nothing to keep yet."""
    with session_scope() as s:
        return bool(s.scalar(select(func.count(User.id)).where(User.role == "ADMIN", User.is_active.is_(True))))


def due(reason: str, now: datetime | None = None) -> bool:
    """True when an automatic backup should be made now for this [reason] ('open', 'timer' or 'quit')."""
    if not enabled() or not _shop_ready():
        return False
    last = _last_bundle()
    if last is not None and (now or datetime.now()) - last < MIN_AGE[reason]:
        return False
    return offsite.content_mark() != (settings.get("auto_offsite_mark") or "")


def run(reason: str) -> dict | None:
    """Makes the automatic backup when it is due. Safe from any thread; never raises (the reason is logged)."""
    if not _lock.acquire(blocking=False):            # one at a time
        return None
    try:
        if not due(reason):
            return None
        info = offsite.create_bundle(LABEL[reason])
        log.info("automatic backup (%s): %s", reason, info.get("name"))
        return info
    except Exception:
        log.exception("automatic backup (%s) failed", reason)
        return None
    finally:
        _lock.release()


def run_in_background(reason: str) -> threading.Thread:
    t = threading.Thread(target=run, args=(reason,), name=f"sdj-auto-backup-{reason}", daemon=True)
    t.start()
    return t
