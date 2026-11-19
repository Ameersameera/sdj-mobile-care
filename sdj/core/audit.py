"""Append-only audit trail."""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select

from ..db.base import session_scope
from ..db.models import AuditLog


def _default(o):
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    return str(o)


def audit(session, action: str, entity: str = "", entity_id=None, details: dict | None = None, *, user=None) -> None:
    from .auth import approved_by, current_user
    u = user or current_user()
    approver = approved_by()
    if approver is not None and approver.id != u.id:
        details = dict(details or {}, approved_by=approver.username)
    session.add(AuditLog(user_id=u.id, username=u.username, action=action, entity=entity,
                         entity_id="" if entity_id is None else str(entity_id),
                         details=json.dumps(details or {}, default=_default, ensure_ascii=False)))


def list_audit(limit: int = 500, search: str = "") -> list[dict]:
    with session_scope() as s:
        q = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
        if search:
            like = f"%{search}%"
            q = q.where((AuditLog.action.like(like)) | (AuditLog.details.like(like)) |
                        (AuditLog.username.like(like)) | (AuditLog.entity.like(like)))
        return [dict(id=a.id, created_at=a.created_at, username=a.username, action=a.action,
                     entity=a.entity, entity_id=a.entity_id, details=a.details) for a in s.scalars(q)]
