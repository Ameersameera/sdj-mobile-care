"""Users, password hashing (PBKDF2-SHA256) and role-based permissions."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select

from ..db.base import session_scope
from ..db.models import User
from .errors import BusinessError, PermissionDenied, ValidationError

ITERATIONS = 240_000

ROLES = ["ADMIN", "MANAGER", "CASHIER", "TECHNICIAN"]
ROLE_LABELS = {"ADMIN": "Admin (Owner)", "MANAGER": "Manager", "CASHIER": "Sales Staff / Cashier",
               "TECHNICIAN": "Technician"}

ALL_PERMISSIONS = [
    "dashboard", "view_profit", "billing", "quick_sale", "sales_history", "cancel_sale",
    "collect_payment", "products_view", "products_edit", "stock_view", "stock_adjust", "purchases",
    "services", "service_history", "spare_parts", "customers", "suppliers", "expenses", "returns",
    "live_summary", "daily_closing", "reports", "accounting", "settings", "users", "backup",
    "reopen_day", "audit_log",
]

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "ADMIN": set(ALL_PERMISSIONS),
    "MANAGER": set(ALL_PERMISSIONS) - {"users", "reopen_day", "settings", "audit_log"},
    "CASHIER": {"dashboard", "billing", "quick_sale", "sales_history", "collect_payment", "products_view",
                "stock_view", "services", "service_history", "spare_parts", "customers", "expenses"},
    "TECHNICIAN": {"dashboard", "services", "service_history", "spare_parts", "products_view", "stock_view",
                   "customers"},
}


@dataclass
class CurrentUser:
    id: int | None
    username: str
    full_name: str
    role: str
    permissions: set[str] = field(default_factory=set)

    def can(self, perm: str) -> bool:
        return perm in self.permissions

    @property
    def display_name(self) -> str:
        return self.full_name or self.username


SYSTEM_USER = CurrentUser(None, "system", "System", "ADMIN", set(ALL_PERMISSIONS))
_current: CurrentUser = SYSTEM_USER


def current_user() -> CurrentUser:
    return _current


def set_current_user(user: CurrentUser | None) -> None:
    global _current
    _current = user or SYSTEM_USER


def require(perm: str) -> None:
    if not _current.can(perm):
        raise PermissionDenied("You do not have permission for this action. Please ask the owner/admin.")


# ---------------------------------------------------------------- hashing
def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt, hexdigest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iters))
        return hmac.compare_digest(digest.hex(), hexdigest)
    except (ValueError, TypeError):
        return False


def _validate_password(password: str) -> None:
    if len(password or "") < 6:
        raise ValidationError("Password must be at least 6 characters long.")


def _to_current(u: User) -> CurrentUser:
    return CurrentUser(u.id, u.username, u.full_name, u.role, set(ROLE_PERMISSIONS.get(u.role, set())))


# ---------------------------------------------------------------- user management
def user_count() -> int:
    with session_scope() as s:
        return s.scalar(select(func.count(User.id))) or 0


def authenticate(username: str, password: str) -> CurrentUser:
    from .audit import audit
    with session_scope() as s:
        u = s.scalar(select(User).where(func.lower(User.username) == (username or "").strip().lower()))
        if u is None or not verify_password(password or "", u.password_hash):
            audit(s, "LOGIN_FAILED", "user", username or "", {"username": username}, user=SYSTEM_USER)
            raise BusinessError("Incorrect username or password.")
        if not u.is_active:
            raise BusinessError("This account is disabled. Please contact the admin.")
        u.last_login_at = datetime.now().replace(microsecond=0)
        cu = _to_current(u)
        audit(s, "LOGIN", "user", u.id, {"username": u.username}, user=cu)
        return cu


def create_user(username: str, password: str, role: str, full_name: str = "", *, check_perm: bool = True) -> int:
    from .audit import audit
    if check_perm:
        require("users")
    username = (username or "").strip()
    if not username or " " in username:
        raise ValidationError("Username is required and cannot contain spaces.")
    if role not in ROLES:
        raise ValidationError("Invalid role.")
    _validate_password(password)
    with session_scope() as s:
        if s.scalar(select(User).where(func.lower(User.username) == username.lower())):
            raise ValidationError(f"Username '{username}' already exists.")
        u = User(username=username, full_name=(full_name or "").strip(), role=role,
                 password_hash=hash_password(password), is_active=True)
        s.add(u)
        s.flush()
        audit(s, "USER_CREATED", "user", u.id, {"username": username, "role": role})
        return u.id


def update_user(user_id: int, *, full_name: str | None = None, role: str | None = None,
                is_active: bool | None = None) -> None:
    from .audit import audit
    require("users")
    with session_scope() as s:
        u = s.get(User, user_id)
        if u is None:
            raise ValidationError("User not found.")
        before = {"role": u.role, "is_active": u.is_active, "full_name": u.full_name}
        if role is not None:
            if role not in ROLES:
                raise ValidationError("Invalid role.")
            u.role = role
        if is_active is not None:
            u.is_active = is_active
        if full_name is not None:
            u.full_name = full_name.strip()
        # never lock out the last active admin
        if before["role"] == "ADMIN" and (u.role != "ADMIN" or not u.is_active):
            admins = s.scalar(select(func.count(User.id)).where(User.role == "ADMIN", User.is_active.is_(True),
                                                                User.id != u.id)) or 0
            if admins == 0:
                raise ValidationError("At least one active admin account is required.")
        audit(s, "USER_UPDATED", "user", u.id, {"before": before,
                                                 "after": {"role": u.role, "is_active": u.is_active,
                                                           "full_name": u.full_name}})


def reset_password(user_id: int, new_password: str) -> None:
    from .audit import audit
    require("users")
    _validate_password(new_password)
    with session_scope() as s:
        u = s.get(User, user_id)
        if u is None:
            raise ValidationError("User not found.")
        u.password_hash = hash_password(new_password)
        audit(s, "PASSWORD_RESET", "user", u.id, {"username": u.username})


def change_own_password(old_password: str, new_password: str) -> None:
    from .audit import audit
    cu = current_user()
    if cu.id is None:
        raise ValidationError("Not logged in.")
    _validate_password(new_password)
    with session_scope() as s:
        u = s.get(User, cu.id)
        if u is None or not verify_password(old_password, u.password_hash):
            raise ValidationError("Current password is incorrect.")
        u.password_hash = hash_password(new_password)
        audit(s, "PASSWORD_CHANGED", "user", u.id, {})


def list_users() -> list[dict]:
    with session_scope() as s:
        return [dict(id=u.id, username=u.username, full_name=u.full_name, role=u.role,
                     role_label=ROLE_LABELS.get(u.role, u.role), is_active=u.is_active,
                     last_login_at=u.last_login_at, created_at=u.created_at)
                for u in s.scalars(select(User).order_by(User.id))]
