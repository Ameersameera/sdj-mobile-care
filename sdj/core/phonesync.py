"""Phone link: pair phones with a one-time code and keep them in sync over the shop Wi-Fi.

No cloud and no internet: the phone talks straight to this PC on the local network.

* Pairing – the owner shows a one-time code (and QR) in Settings → Phone link. The phone proves it knows the
  code without sending it (both sides derive an AES key from it), and receives its own 256-bit device key.
* Sync – every request and answer is AES-256-GCM encrypted with the device key; a counter stops replays.
* The PC is the main copy. The phone keeps a copy for offline use and sends what it entered as "entries"
  (bill, payment, expense, repair job …). Each entry is saved here through the normal shop rules, exactly once,
  and the phone then receives fresh data. Anything that needs the owner's eye becomes a Sync alert.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import socket
import sqlite3
import threading
import uuid
import zlib
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import func, select, text

from .. import APP_VERSION
from ..db import base
from ..db.base import q2, session_scope
from ..db.models import (Customer, DailyClosing, Expense, Payment, Sale, ServiceItem, ServiceJob, SyncAlert,
                         SyncDevice, SyncOp, User)
from . import settings, syncctx
from .audit import audit
from .auth import (ROLE_PERMISSIONS, CurrentUser, acting_as, approved, check_own_password, current_user, require,
                   user_by_id)
from .business_day import closed_dates, current_business_date, is_day_closed
from .errors import BusinessError, DayClosedError, DuplicateError, NotFoundError, PermissionDenied, ValidationError
from .numbering import _next, financial_year_label

log = logging.getLogger(__name__)

PROTOCOL = 1
PORT = 47810
DISCOVERY_PORT = 47811
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LEN = 10
CODE_MINUTES = 10
PAIR_ITERATIONS = 100_000
MAX_OPS = 400
CREATE_KINDS = {"sale.new", "cust.pay", "exp.new", "job.new", "job.pay", "job.part"}
KIND_LABELS = {"sale.new": "New bill", "sale.edit": "Bill edit", "sale.cancel": "Bill cancel",
               "cust.pay": "Customer payment", "exp.new": "Expense", "job.new": "New repair job",
               "job.edit": "Repair job change", "job.status": "Repair status", "job.pay": "Repair payment",
               "job.deliver": "Repair delivery", "job.part": "Spare part on job"}
ALERT_LABELS = {"NEGATIVE_STOCK": "Last piece sold twice", "IMEI": "IMEI sold twice", "CLOSED_DAY": "Closed day",
                "BILL_CONFLICT": "Bill changed on both", "FAILED": "Entry not saved", "TOTAL": "Total differs",
                "HIDDEN_PRODUCT": "Hidden product sold", "EXTRA_PAYMENT": "Extra payment", "DATE": "Phone date"}


class SyncError(BusinessError):
    """Refused sync / pairing request (sent back to the phone as a plain error)."""

    def __init__(self, message: str, code: str = "error", status: int = 400):
        super().__init__(message)
        self.code, self.status = code, status


class BillConflict(BusinessError):
    pass


# ================================================================ small helpers
def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def rupees(paise) -> Decimal:
    return (Decimal(int(paise or 0)) / 100).quantize(Decimal("0.01"))


def paise(value) -> int:
    return int((Decimal(str(value or 0)) * 100).quantize(Decimal("1")))


def _date(v) -> date | None:
    if not v:
        return None
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def _dt(v) -> datetime | None:
    try:
        return datetime.fromisoformat(str(v)[:19]) if v else None
    except ValueError:
        return None


def seal(key: bytes, obj, aad: bytes) -> dict:
    raw = json.dumps(obj, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")
    nonce = os.urandom(12)
    return {"n": _b64e(nonce), "c": _b64e(AESGCM(key).encrypt(nonce, zlib.compress(raw, 6), aad))}


def unseal(key: bytes, env: dict, aad: bytes):
    try:
        data = AESGCM(key).decrypt(_b64d(env["n"]), _b64d(env["c"]), aad)
        return json.loads(zlib.decompress(data).decode("utf-8"))
    except (InvalidTag, KeyError, ValueError, TypeError, zlib.error) as exc:
        raise SyncError("Could not read the request (wrong key).", "bad_key", 403) from exc


def normalise_code(code: str) -> str:
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


def pair_key(code: str, pc_id: str) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", normalise_code(code).encode(), f"SDJ-LINK:{pc_id}".encode(),
                               PAIR_ITERATIONS, 32)


def pc_identity() -> str:
    pid = settings.get("phone_pc_id") or ""
    if not pid:
        pid = secrets.token_hex(16)
        settings.set_value("phone_pc_id", pid)
    return pid


def local_ips() -> list[str]:
    """IPv4 addresses of this PC on the local network (the phone needs one of them)."""
    found = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))          # no packet is sent; just picks the main network card
        found.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    out = []
    for ip in found:
        if ip.startswith(("127.", "169.254.", "0.")) or ip in out:
            continue
        out.append(ip)
    return out


def qr_matrix(data: str) -> list[list[bool]]:
    from reportlab.graphics.barcode import qrencoder
    for version in range(1, 41):
        try:
            q = qrencoder.QRCode(version, qrencoder.QRErrorCorrectLevel.M)
            q.addData(data)
            q.make()
            n = q.getModuleCount()
            return [[bool(q.isDark(r, c)) for c in range(n)] for r in range(n)]
        except Exception:  # data does not fit this version – try the next size
            continue
    raise ValueError("QR data too long")


# ================================================================ one-time link codes (memory only)
@dataclass
class LinkCode:
    code: str
    user_id: int
    username: str
    created_by: str
    expires: datetime
    failures: int = 0


_codes: dict[str, LinkCode] = {}
_codes_lock = threading.Lock()
_listeners: list = []          # callables(event: str, info: dict) – the UI subscribes


def subscribe(fn) -> None:
    if fn not in _listeners:
        _listeners.append(fn)


def unsubscribe(fn) -> None:
    if fn in _listeners:
        _listeners.remove(fn)


def _emit(event: str, **info) -> None:
    for fn in list(_listeners):
        try:
            fn(event, info)
        except Exception:  # a broken listener must never break syncing
            log.exception("sync listener failed")


def _owner_check() -> None:
    require("settings")


def new_link_code(user_id: int) -> dict:
    """A fresh one-time code (valid 10 minutes) that links one phone to *user_id*'s account."""
    _owner_check()
    with session_scope() as s:
        u = s.get(User, user_id)
        if u is None or not u.is_active:
            raise ValidationError("Choose an active user account for the phone.")
        username = u.username
    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))
    info = LinkCode(code, user_id, username, current_user().display_name, _now() + timedelta(minutes=CODE_MINUTES))
    with _codes_lock:
        _codes.clear()                    # only one code is valid at a time
        _codes[code] = info
    with session_scope() as s:
        audit(s, "PHONE_CODE_CREATED", "user", user_id, {"for_user": username})
    _emit("code")
    return code_info(info)


def code_info(c: LinkCode) -> dict:
    ips = local_ips()
    name = (settings.get("shop_name") or "SDJ").replace(";", " ")
    qr = f"SDJLINK:{PROTOCOL};h={','.join(ips)};p={PORT};c={c.code};id={pc_identity()};n={name}"
    return dict(code=c.code, display=f"{c.code[:5]}-{c.code[5:]}", username=c.username, expires=c.expires,
                ips=ips, port=PORT, qr=qr)


def active_code() -> dict | None:
    with _codes_lock:
        for c in list(_codes.values()):
            if c.expires <= _now():
                _codes.pop(c.code, None)
        c = next(iter(_codes.values()), None)
    return code_info(c) if c else None


def cancel_link_code() -> None:
    with _codes_lock:
        _codes.clear()
    _emit("code")


def handle_pair(env: dict, ip: str = "") -> dict:
    """A phone sends {pc, n, c} encrypted with the key derived from the code it was given."""
    pc_id = pc_identity()
    if env.get("pc") and env["pc"] != pc_id:
        raise SyncError("This code belongs to another computer.", "wrong_pc", 403)
    with _codes_lock:
        live = [c for c in _codes.values() if c.expires > _now()]
    found = req = key = None
    for c in live:
        k = pair_key(c.code, pc_id)
        try:
            req = unseal(k, env, b"pair")
            found, key = c, k
            break
        except SyncError:
            continue
    if found is None:
        with _codes_lock:
            for c in live:
                c.failures += 1
                if c.failures >= 8:                 # too many wrong tries – the code stops working
                    _codes.pop(c.code, None)
        raise SyncError("The code is wrong or has expired. Create a new code on the PC.", "bad_code", 403)
    with _codes_lock:
        _codes.pop(found.code, None)                # one-time
    with session_scope() as s:
        u = s.get(User, found.user_id)
        if u is None or not u.is_active:
            raise SyncError("The user account for this phone is disabled.", "user_disabled", 403)
        dev = SyncDevice(device_uid=uuid.uuid4().hex, device_no=_next(s, "phone_device_no"),
                         name=str(req.get("name") or "Phone")[:80], user_id=u.id, secret=_b64e(os.urandom(32)),
                         linked_by=found.created_by, last_ip=ip, app_version=str(req.get("app") or "")[:20])
        s.add(dev)
        s.flush()
        audit(s, "PHONE_LINKED", "sync_device", dev.id, {"name": dev.name, "user": u.username, "ip": ip,
                                                         "series": f"P{dev.device_no}"}, user=_as_cu(u))
        answer = dict(ok=True, hello=req.get("hello"), device=dev.device_uid, key=dev.secret, no=dev.device_no,
                      pc=pc_id, shop=settings.get("shop_name") or "SDJ Mobile Care",
                      user=dict(id=u.id, username=u.username, name=u.full_name or u.username, role=u.role))
        dev_name = dev.name
    _emit("linked", name=dev_name)
    return seal(key, answer, b"pair")


def _as_cu(u: User) -> CurrentUser:
    return CurrentUser(u.id, u.username, u.full_name, u.role, set(ROLE_PERMISSIONS.get(u.role, set())))


# ================================================================ devices (Settings → Phone link)
def list_devices(include_removed: bool = False) -> list[dict]:
    with session_scope() as s:
        q = select(SyncDevice).order_by(SyncDevice.id)
        if not include_removed:
            q = q.where(SyncDevice.status == "ACTIVE")
        out = []
        for d in s.scalars(q):
            u = s.get(User, d.user_id)
            alerts = s.scalar(select(func.count(SyncAlert.id)).where(SyncAlert.device_id == d.id,
                                                                    SyncAlert.status == "OPEN")) or 0
            mem = _seen.get(d.device_uid) or {}
            out.append(dict(id=d.id, name=d.name, series=f"P{d.device_no}", username=u.username if u else "?",
                            user_name=(u.full_name or u.username) if u else "?", status=d.status,
                            linked_at=d.linked_at, last_seen_at=mem.get("at") or d.last_seen_at,
                            last_ip=mem.get("ip") or d.last_ip,
                            app_version=d.app_version, alerts=alerts,
                            keeps_backups=bool(d.backups) if d.backups is not None else bool(u and u.role == "ADMIN")))
        return out


def active_device_count() -> int:
    with session_scope() as s:
        return s.scalar(select(func.count(SyncDevice.id)).where(SyncDevice.status == "ACTIVE")) or 0


def remove_device(device_id: int, password: str) -> None:
    """Stop a phone from syncing (for a lost or replaced phone). Its old entries stay."""
    _owner_check()
    check_own_password(password)
    with session_scope() as s:
        d = s.get(SyncDevice, device_id)
        if d is None or d.status != "ACTIVE":
            raise NotFoundError("Phone not found.")
        d.status, d.removed_at, d.removed_by = "REMOVED", _now(), current_user().display_name
        audit(s, "PHONE_REMOVED", "sync_device", d.id, {"name": d.name, "series": f"P{d.device_no}"})
    _emit("removed")


# ================================================================ alerts
def _alert(s, dev_id: int | None, kind: str, title: str, details: str = "", *, ref_type: str = "",
           ref_id: int | None = None, ref_date: date | None = None, payload=None) -> None:
    if kind == "CLOSED_DAY":
        old = s.scalar(select(SyncAlert).where(SyncAlert.kind == "CLOSED_DAY", SyncAlert.status == "OPEN",
                                               SyncAlert.ref_date == ref_date))
        if old is not None:
            old.details = (old.details + "\n" + details).strip()[-4000:]
            return
    s.add(SyncAlert(device_id=dev_id, kind=kind, title=title[:200], details=details, ref_type=ref_type,
                    ref_id=ref_id, ref_date=ref_date, payload=json.dumps(payload, default=str) if payload else ""))


def open_alert_count() -> int:
    with session_scope() as s:
        return s.scalar(select(func.count(SyncAlert.id)).where(SyncAlert.status == "OPEN")) or 0


def list_alerts(status: str | None = "OPEN", limit: int = 500) -> list[dict]:
    with session_scope() as s:
        q = select(SyncAlert).order_by(SyncAlert.id.desc()).limit(limit)
        if status:
            q = q.where(SyncAlert.status == status)
        names = {d.id: d.name for d in s.scalars(select(SyncDevice))}
        return [dict(id=a.id, created_at=a.created_at, phone=names.get(a.device_id, ""), kind=a.kind,
                     kind_label=ALERT_LABELS.get(a.kind, a.kind), title=a.title, details=a.details,
                     ref_type=a.ref_type, ref_id=a.ref_id, ref_date=a.ref_date, status=a.status,
                     resolution=a.resolution, resolved_by=a.resolved_by, resolved_at=a.resolved_at,
                     can_use_phone=a.kind == "BILL_CONFLICT" and a.status == "OPEN" and bool(a.payload),
                     can_update_closing=a.kind == "CLOSED_DAY" and a.status == "OPEN")
                for a in s.scalars(q)]


def resolve_alert(alert_id: int, resolution: str = "Checked") -> None:
    with session_scope() as s:
        a = s.get(SyncAlert, alert_id)
        if a is None:
            raise NotFoundError("Alert not found.")
        a.status, a.resolved_at, a.resolved_by = "RESOLVED", _now(), current_user().display_name
        a.resolution = resolution
        audit(s, "SYNC_ALERT_RESOLVED", "sync_alert", a.id, {"kind": a.kind, "title": a.title,
                                                            "resolution": resolution})
    _emit("alerts")


def update_closing_from_alert(alert_id: int) -> None:
    """Recalculate a closed day's figures so they include the phone entries that arrived later."""
    require("daily_closing")
    with session_scope() as s:
        a = s.get(SyncAlert, alert_id)
        if a is None or a.kind != "CLOSED_DAY" or a.ref_date is None:
            raise NotFoundError("Alert not found.")
        d = a.ref_date
    from .reset import _refresh_closings
    _refresh_closings({d}, note=f"Figures updated on {date.today():%d %b %Y} to include entries made on a phone.")
    resolve_alert(alert_id, f"Closing of {d:%d %b %Y} updated")


def use_phone_version(alert_id: int) -> None:
    """Bill conflict: apply the phone's change on top of the PC's current bill (the owner decided)."""
    with session_scope() as s:
        a = s.get(SyncAlert, alert_id)
        if a is None or a.kind != "BILL_CONFLICT" or not a.payload or a.status != "OPEN":
            raise NotFoundError("Alert not found.")
        op = json.loads(a.payload)
        dev = s.get(SyncDevice, a.device_id) if a.device_id else None
        if dev is None:
            raise NotFoundError("The phone for this alert was removed.")
        s.expunge(dev)
    op = dict(op, force=True)
    batch = _Batch(dev)
    with syncctx.phone_entry(syncctx.PhoneEntry(dev.device_uid, _date(op.get("d")))):
        HANDLERS[op["k"]](batch, op)
    resolve_alert(alert_id, "Phone version applied")


# ================================================================ applying entries from a phone
@dataclass
class _Batch:
    dev: SyncDevice
    created_sales: set = field(default_factory=set)


def _resolve_customer(c) -> int | None:
    """Existing customer id, a new customer from the phone (matched by phone number), or walk-in (None)."""
    if not c:
        return None
    if c.get("id"):
        with session_scope() as s:
            if s.get(Customer, int(c["id"])) is not None:
                return int(c["id"])
    if c.get("phone") or c.get("name"):
        from .repairs import find_or_create_customer
        with session_scope() as s:
            return find_or_create_customer(s, c.get("name") or "", c.get("phone") or "", c.get("address") or "").id
    return None


def _target_sale(s, t: dict, op: dict | None = None) -> Sale:
    sale = None
    if t.get("ref"):
        sale = s.scalar(select(Sale).where(Sale.client_ref == t["ref"]))
    if sale is None and t.get("id"):
        sale = s.get(Sale, int(t["id"]))
    if sale is None:
        raise NotFoundError("The bill was not found on the PC.")
    if op and op.get("force") and sale.status != "COMPLETED":
        number = (op.get("base") or {}).get("no") or sale.invoice_no.split("-E")[0]
        now = s.scalar(select(Sale).where(Sale.invoice_no == number, Sale.status == "COMPLETED"))
        if now is None:
            raise BusinessError(f"Bill {number} is cancelled on the PC, so the phone's change cannot be applied.")
        sale = now
    return sale


def _target_job(t: dict) -> int:
    with session_scope() as s:
        job = None
        if t.get("ref"):
            job = s.scalar(select(ServiceJob).where(ServiceJob.client_ref == t["ref"]))
        if job is None and t.get("id"):
            job = s.get(ServiceJob, int(t["id"]))
        if job is None:
            raise NotFoundError("The repair job was not found on the PC.")
        return job.id


def _cart(items: list[dict]):
    from .sales import CartItem
    out = []
    for i in items or []:
        out.append(CartItem(product_id=int(i["p"]), quantity=int(i["q"]), unit_price=rupees(i.get("price")),
                            discount=rupees(i.get("disc")), imei_record_id=int(i["imei_id"]) if i.get("imei_id") else None,
                            imei_text=str(i.get("imei") or "")))
    return out


def _pays(p: dict | None) -> dict:
    return {str(m): rupees(v) for m, v in (p or {}).items() if int(v or 0) > 0}


def _series_ok(batch: _Batch, number: str, kind: str) -> None:
    if kind == "sale":
        pre = f"{settings.get('invoice_prefix') or 'SDJ'}-P{batch.dev.device_no}/"
    else:
        pre = f"{settings.get('job_prefix') or 'JOB'}-P{batch.dev.device_no}-"
    if not (number or "").startswith(pre):
        raise ValidationError(f"Number {number} is not in this phone's series ({pre}…).")


def _h_sale_new(batch: _Batch, op: dict) -> dict:
    from .sales import _create_sale
    kind = op.get("type") or "BILL"
    require("quick_sale" if kind == "QUICK" else "billing")
    _series_ok(batch, op.get("no", ""), "sale")
    entry = syncctx.current()
    cust_id = _resolve_customer(op.get("cust"))
    with session_scope() as s:
        sale = _create_sale(s, _cart(op.get("items")), customer_id=cust_id, bill_discount=rupees(op.get("bdisc")),
                            payments=_pays(op.get("pay")), notes=op.get("notes") or "", client_ref=op["ref"],
                            sale_type=kind, invoice_no=op["no"], business_date=entry.business_date)
        batch.created_sales.add(sale.id)
        res = dict(id=sale.id, no=sale.invoice_no, total=paise(sale.grand_total))
        if op.get("total") is not None and int(op["total"]) != res["total"]:
            _alert(s, batch.dev.id, "TOTAL", f"Bill {sale.invoice_no}: total differs",
                   f"Phone showed ₹{rupees(op['total']):,.2f}, the PC saved ₹{sale.grand_total:,.2f} "
                   "(PC rules used). Please check the bill.", ref_type="SALE", ref_id=sale.id)
        return res


def _check_base(sale: Sale, op: dict) -> None:
    if op.get("force"):
        return
    b = op.get("base") or {}
    if sale.status != "COMPLETED":
        raise BillConflict("The bill was cancelled or edited on the PC after the phone last synced.")
    if b.get("no") and b["no"] != sale.invoice_no:
        raise BillConflict("The bill was edited on the PC after the phone last synced.")
    if b.get("paid") is not None and int(b["paid"]) != paise(sale.paid_amount):
        raise BillConflict("A payment for this bill was recorded on the PC after the phone last synced.")


def _h_sale_edit(batch: _Batch, op: dict) -> dict:
    from .sales import amend_sale
    entry = syncctx.current()
    with session_scope() as s:
        sale = _target_sale(s, op.get("target") or {}, op)
        sale_id = sale.id
        _check_base(sale, op)
        entry.allow_closed_day = sale.id in batch.created_sales     # made and edited on the phone, same trip
    cust_id = _resolve_customer(op.get("cust"))
    try:
        r = amend_sale(sale_id, _cart(op.get("items")), customer_id=cust_id, bill_discount=rupees(op.get("bdisc")),
                       payments=_pays(op.get("pay")), notes=op.get("notes") or "", client_ref=op["ref"])
    except DayClosedError as exc:
        raise BillConflict(str(exc)) from exc
    batch.created_sales.add(r["id"])
    return dict(id=r["id"], no=r["invoice_no"], total=paise(r["grand_total"]), old=r["old_version"])


def _h_sale_cancel(batch: _Batch, op: dict) -> dict:
    from .sales import cancel_sale
    entry = syncctx.current()
    with session_scope() as s:
        sale = _target_sale(s, op.get("target") or {}, op)
        sale_id, number = sale.id, sale.invoice_no
        if sale.status == "CANCELLED" and op.get("base", {}).get("no") == number:
            return dict(id=sale_id, no=number)        # already cancelled (resent entry)
        _check_base(sale, op)
        entry.allow_closed_day = sale.id in batch.created_sales
    try:
        cancel_sale(sale_id, op.get("reason") or "Cancelled on phone")
    except DayClosedError as exc:
        raise BillConflict(str(exc)) from exc
    return dict(id=sale_id, no=number)


def _h_cust_pay(batch: _Batch, op: dict) -> dict:
    from .parties import _outstanding, collect_customer_payment
    cust_id = _resolve_customer(op.get("cust"))
    if not cust_id:
        raise ValidationError("Customer not found for this payment.")
    amount = rupees(op.get("amount"))
    with session_scope() as s:
        due = sum((d["due"] for d in _outstanding(s, s.get(Customer, cust_id))), Decimal("0.00"))
        name = s.get(Customer, cust_id).name
    take = min(amount, due)
    if take <= 0:
        raise ValidationError(f"{name} has no pending balance on the PC, so ₹{amount:,.2f} could not be matched. "
                              "It was probably collected on the PC too – please check.")
    collect_customer_payment(cust_id, take, op.get("mode") or "CASH", op.get("notes") or "Collected on phone",
                             client_ref=op["ref"])
    if take < amount:
        with session_scope() as s:
            _alert(s, batch.dev.id, "EXTRA_PAYMENT", f"{name}: ₹{amount - take:,.2f} extra collected on phone",
                   f"The phone recorded ₹{amount:,.2f} but only ₹{take:,.2f} was pending on the PC. "
                   "Please check and refund or adjust the extra amount.")
    return dict(customer=cust_id, amount=paise(take))


def _h_exp_new(batch: _Batch, op: dict) -> dict:
    from .expenses import add_expense
    entry = syncctx.current()
    eid = add_expense(op.get("cat") or "Miscellaneous", rupees(op.get("amount")), description=op.get("desc") or "",
                      payment_mode=op.get("mode") or "CASH", expense_date=entry.business_date,
                      client_ref=op["ref"])
    return dict(id=eid)


def _h_job_new(batch: _Batch, op: dict) -> dict:
    from .repairs import create_job
    _series_ok(batch, op.get("no", ""), "job")
    entry = syncctx.current()
    data = {k: op.get(k) for k in ("customer_name", "customer_phone", "address", "brand", "model", "imei",
                                   "accessories", "problem", "technician", "notes", "warranty_days")}
    data["labour_charge"] = rupees(op.get("labour"))
    data["estimated_delivery"] = _date(op.get("est"))
    if data["estimated_delivery"] and data["estimated_delivery"] < entry.business_date:
        data["estimated_delivery"] = None
    if data.get("warranty_days") is None:
        data.pop("warranty_days")
    r = create_job(data, advance=rupees(op.get("adv")), advance_mode=op.get("adv_mode") or "CASH",
                   client_ref=op["ref"], job_no=op["no"])
    return dict(id=r["id"], no=r["job_no"])


def _h_job_edit(batch: _Batch, op: dict) -> dict:
    from .repairs import update_job
    jid = _target_job(op.get("target") or {})
    f = dict(op.get("fields") or {})
    for k in ("labour_charge", "discount"):
        if k in f:
            f[k] = rupees(f[k])
    if "estimated_delivery" in f:
        f["estimated_delivery"] = _date(f["estimated_delivery"])
    update_job(jid, f)
    return dict(id=jid)


def _h_job_status(batch: _Batch, op: dict) -> dict:
    from .repairs import set_status
    jid = _target_job(op.get("target") or {})
    set_status(jid, op.get("status") or "", reason=op.get("reason") or "", refund_mode=op.get("refund_mode") or "CASH")
    return dict(id=jid)


def _h_job_pay(batch: _Batch, op: dict) -> dict:
    from .repairs import add_job_payment
    jid = _target_job(op.get("target") or {})
    pid = add_job_payment(jid, rupees(op.get("amount")), op.get("mode") or "CASH", op.get("notes") or "",
                          client_ref=op["ref"])
    return dict(id=jid, payment=pid)


def _h_job_deliver(batch: _Batch, op: dict) -> dict:
    from .repairs import deliver_job
    jid = _target_job(op.get("target") or {})
    with session_scope() as s:
        job = s.get(ServiceJob, jid)
        if job.status == "DELIVERED":
            if int(op.get("collect") or 0) > 0:
                _alert(s, batch.dev.id, "EXTRA_PAYMENT", f"Job {job.job_no} was already delivered on the PC",
                       f"The phone collected ₹{rupees(op['collect']):,.2f} at delivery. "
                       "Please check that it is not counted twice.", ref_type="SERVICE", ref_id=jid)
            return dict(id=jid)
    r = deliver_job(jid, rupees(op.get("collect")), op.get("mode") or "CASH", op.get("refund_mode") or "CASH")
    return dict(id=jid, balance=paise(r["balance"]))


def _h_job_part(batch: _Batch, op: dict) -> dict:
    from .repairs import add_part
    jid = _target_job(op.get("target") or {})
    price = rupees(op["price"]) if op.get("price") is not None else None
    iid = add_part(jid, int(op["p"]), int(op.get("q") or 1), price, client_ref=op["ref"])
    return dict(id=jid, item=iid)


HANDLERS = {"sale.new": _h_sale_new, "sale.edit": _h_sale_edit, "sale.cancel": _h_sale_cancel,
            "cust.pay": _h_cust_pay, "exp.new": _h_exp_new, "job.new": _h_job_new, "job.edit": _h_job_edit,
            "job.status": _h_job_status, "job.pay": _h_job_pay, "job.deliver": _h_job_deliver,
            "job.part": _h_job_part}


def _op_summary(op: dict) -> str:
    k = op.get("k")
    if k in ("sale.new", "sale.edit"):
        total = sum(int(v or 0) for v in (op.get("pay") or {}).values())
        return f"{op.get('no') or ''} · {len(op.get('items') or [])} item(s) · paid ₹{rupees(total):,.2f}"
    if k in ("cust.pay", "exp.new", "job.pay"):
        return f"₹{rupees(op.get('amount')):,.2f} {op.get('mode') or ''} {op.get('cat') or ''}".strip()
    if k == "job.new":
        return f"{op.get('no')} · {op.get('customer_name')} · {op.get('brand')} {op.get('model')}"
    return json.dumps({x: op[x] for x in op if x not in ("id", "k", "at", "u", "ap", "apf")}, default=str)[:300]


def apply_op(dev: SyncDevice, user: CurrentUser, op: dict, batch: _Batch) -> dict:
    uid, kind = str(op.get("id") or ""), str(op.get("k") or "")
    if not uid or kind not in HANDLERS:
        return dict(id=uid, st="FAILED", msg="This entry type is not known to the PC. Update the PC app.")
    with session_scope() as s:
        done = s.scalar(select(SyncOp).where(SyncOp.op_uid == uid))
        if done is not None:            # already saved (the phone did not get the answer last time)
            return dict(id=uid, st=done.status, msg=done.message, res=json.loads(done.result or "{}"))
        bdate = current_business_date(s)
    day = _date(op.get("d")) or bdate
    date_note = ""
    if day > date.today() + timedelta(days=1):
        date_note = f"The phone's date ({day:%d %b %Y}) was ahead of the PC, so the PC's date was used."
        day = bdate
    entry = syncctx.PhoneEntry(dev.device_uid, day, allow_closed_day=kind in CREATE_KINDS)
    with session_scope() as s:
        day_closed = is_day_closed(s, day)
    approver = user_by_id(int(op["ap"])) if op.get("ap") else None
    perms = set(op.get("apf") or []) & (approver.permissions if approver else set())
    status, msg, res = "DONE", "", {}
    try:
        with acting_as(user), syncctx.phone_entry(entry):
            with (approved(approver, perms) if approver and perms else nullcontext()):
                res = HANDLERS[kind](batch, op) or {}
    except DuplicateError:
        status, msg = "DONE", "Already saved"
    except BillConflict as exc:
        status, msg = "CONFLICT", str(exc)
    except (BusinessError, ValueError, KeyError, TypeError) as exc:
        status, msg = "FAILED", str(exc) or exc.__class__.__name__
    except Exception as exc:  # never let one entry stop the others
        log.exception("phone entry %s failed", uid)
        status, msg = "FAILED", f"Unexpected error: {exc}"
    if status == "DONE" and day_closed and kind not in ("sale.edit", "sale.cancel", "job.edit"):
        entry.closed_days.add(day)
    with session_scope() as s:
        label = KIND_LABELS.get(kind, kind)
        ref_type = "SALE" if kind.startswith("sale") else ("SERVICE" if kind.startswith("job") else "")
        ref_id = res.get("id") if ref_type else None
        number = res.get("no") or op.get("no") or ""
        if status == "CONFLICT":
            _alert(s, dev.id, "BILL_CONFLICT", f"{label} from phone not applied: {number or 'bill'}",
                   f"{msg}\nChoose 'Use phone version' to apply the phone's change, or 'Keep PC version'.",
                   ref_type="SALE", ref_id=(op.get("target") or {}).get("id"), payload=op)
        elif status == "FAILED":
            _alert(s, dev.id, "FAILED", f"{label} from phone was not saved", f"{msg}\n{_op_summary(op)}",
                   ref_type=ref_type, ref_id=ref_id)
        for name, qty in entry.negative_stock:
            _alert(s, dev.id, "NEGATIVE_STOCK", f"{name}: stock is now {qty}",
                   f"{label} {number} was saved from the phone, but the PC had already sold the last piece(s). "
                   "Count the stock, then return or cancel one of the bills if needed.", ref_type=ref_type,
                   ref_id=ref_id)
        for imei, pname in entry.imei_conflicts:
            _alert(s, dev.id, "IMEI", f"IMEI {imei} ({pname}) is on two bills",
                   f"{label} {number} from the phone has an IMEI that the PC had already sold or removed. "
                   "Check which bill is right and cancel or return the other.", ref_type=ref_type, ref_id=ref_id)
        for pname in entry.inactive_products:
            _alert(s, dev.id, "HIDDEN_PRODUCT", f"Hidden product sold on phone: {pname}",
                   f"{label} {number} includes a product that was hidden on the PC.", ref_type=ref_type,
                   ref_id=ref_id)
        for d in sorted(entry.closed_days):
            _alert(s, dev.id, "CLOSED_DAY", f"Phone entries for {d:%d %b %Y} (day already closed)",
                   f"{label} {number}".strip(), ref_type="DATE", ref_date=d)
        if date_note:
            _alert(s, dev.id, "DATE", f"{label} {number}: phone date corrected", date_note, ref_type=ref_type,
                   ref_id=ref_id)
        s.add(SyncOp(op_uid=uid, device_id=dev.id, kind=kind, made_at=_dt(op.get("at")), business_date=day,
                     status=status, result=json.dumps(res, default=str), message=msg[:2000]))
    return dict(id=uid, st=status, msg=msg, res=res)


# ================================================================ data sent to the phone
_boot = secrets.token_hex(4)
_watch = {"key": None, "conn": None}


def _close_watch() -> None:
    if _watch["conn"] is not None:
        try:
            _watch["conn"].close()
        except sqlite3.Error:
            pass
    _watch["conn"], _watch["key"] = None, None


base.on_dispose(_close_watch)
_cache_lock = threading.Lock()
_cache: dict = {}


def data_token() -> str:
    """Changes whenever the database changes (any connection), the day changes, or the app restarts."""
    with base.db_lock:
        path = str(base.current_db_path() or "")
        key = (path, id(base._db.engine))
        try:
            if _watch["key"] != key or _watch["conn"] is None:
                _close_watch()
                _watch["conn"] = sqlite3.connect(path, check_same_thread=False)
                _watch["key"] = key
            v = _watch["conn"].execute("PRAGMA data_version").fetchone()[0]
        except sqlite3.Error:
            _close_watch()
            v = secrets.token_hex(3)
    return f"{_boot}:{v}:{date.today().isoformat()}"


def _rows(s, sql: str, **params) -> list[dict]:
    res = s.execute(text(sql), params)
    keys = list(res.keys())
    return [dict(zip(keys, r)) for r in res]


def _history_cutoff() -> str:
    days = int(settings.get("phone_history_days") or 45)
    return (date.today() - timedelta(days=max(days, 7))).isoformat()


def _common_data() -> dict:
    """Products, stock, IMEIs, customers with dues, recent bills, repair jobs, expenses and today's figures."""
    from .parties import _balance_from, _customer_aggregates
    from .reports import cash_position
    from .closing import suggested_opening_cash
    cut = _history_cutoff()
    out: dict = {}
    with session_scope() as s:
        closed = {d.isoformat() for d in closed_dates(s)}
        out["categories"] = [dict(id=r["id"], name=r["name"], grp=r["grp"], imei=bool(r["requires_imei"]),
                                  on=bool(r["is_active"]))
                             for r in _rows(s, "SELECT id, name, grp, requires_imei, is_active FROM categories "
                                               "ORDER BY sort_order, name")]
        out["products"] = [dict(id=r["id"], code=r["code"], name=r["name"], brand=r["brand"] or "",
                                cat=r["category_id"], model=r["model_number"] or "", bc=r["barcode"] or "",
                                price=r["selling_price"], gst=r["gst_rate"], hsn=r["hsn_code"] or "",
                                qty=r["quantity"], min=r["min_stock"], imei=bool(r["track_imei"]),
                                on=bool(r["is_active"]), wm=r["warranty_months"] or 0)
                           for r in _rows(s, "SELECT id, code, name, brand, category_id, model_number, barcode, "
                                             "selling_price, gst_rate, hsn_code, quantity, min_stock, track_imei, "
                                             "is_active, warranty_months FROM products ORDER BY name")]
        out["imeis"] = [dict(id=r["id"], p=r["product_id"], i1=r["imei1"], i2=r["imei2"] or "", sn=r["serial_no"] or "")
                        for r in _rows(s, "SELECT id, product_id, imei1, imei2, serial_no FROM imei_records "
                                          "WHERE status='IN_STOCK' ORDER BY id")]
        agg = _customer_aggregates(s)
        out["customers"] = [dict(id=c.id, name=c.name, phone=c.phone or "", addr=c.address or "",
                                 due=paise(_balance_from(c, agg.get(c.id))))
                            for c in s.scalars(select(Customer).where(Customer.is_active.is_(True),
                                                                      Customer.is_walk_in.is_(False))
                                               .order_by(Customer.name))]
        sales = _rows(s, "SELECT id, invoice_no, client_ref, sale_type, customer_id, customer_name, customer_phone, "
                         "business_date, created_at, gst_enabled, price_includes_tax, gross_amount, item_discount, "
                         "bill_discount, taxable_amount, gst_amount, round_off, grand_total, paid_amount, "
                         "returned_amount, refunded_amount, status, notes, cancel_reason FROM sales "
                         "WHERE business_date >= :cut ORDER BY id DESC", cut=cut)
        ids = [r["id"] for r in sales]
        items: dict[int, list] = {}
        pays: dict[int, list] = {}
        returned = set()
        if ids:
            id_list = ",".join(str(i) for i in ids)
            for r in _rows(s, "SELECT id, sale_id, product_id, product_name, imei_record_id, imei_text, quantity, "
                              "unit_price, item_discount, bill_discount_share, taxable_value, gst_rate, gst_amount, "
                              f"line_total, returned_qty, warranty_months FROM sale_items WHERE sale_id IN ({id_list}) "
                              "ORDER BY id"):
                items.setdefault(r["sale_id"], []).append(dict(
                    p=r["product_id"], name=r["product_name"], imei_id=r["imei_record_id"], imei=r["imei_text"] or "",
                    q=r["quantity"], price=r["unit_price"], disc=r["item_discount"], share=r["bill_discount_share"],
                    taxable=r["taxable_value"], rate=r["gst_rate"], gst=r["gst_amount"], total=r["line_total"],
                    ret=r["returned_qty"], wm=r["warranty_months"] or 0))
            for r in _rows(s, "SELECT ref_id, business_date, direction, mode, amount, kind, status FROM payments "
                              f"WHERE ref_type='SALE' AND ref_id IN ({id_list}) ORDER BY id"):
                pays.setdefault(r["ref_id"], []).append(dict(d=r["business_date"], dir=r["direction"], mode=r["mode"],
                                                             amt=r["amount"], kind=r["kind"], st=r["status"]))
            returned = {r["sale_id"] for r in _rows(s, f"SELECT DISTINCT sale_id FROM returns WHERE sale_id IN ({id_list})")}
        out["sales"] = []
        for r in sales:
            plist = pays.get(r["id"], [])
            if r["status"] != "COMPLETED":
                blk = "This bill is already cancelled."
            elif r["sale_type"] == "EXCHANGE":
                blk = "Exchange bills can only be changed from Returns & Exchanges on the PC."
            elif r["id"] in returned:
                blk = "Items from this bill were already returned, so it cannot be changed."
            elif str(r["business_date"]) in closed or any(p["st"] == "ACTIVE" and str(p["d"]) in closed for p in plist):
                blk = "This day is closed on the PC, so the bill cannot be changed."
            else:
                blk = ""
            due = (r["grand_total"] - r["returned_amount"]) - (r["paid_amount"] - r["refunded_amount"])
            out["sales"].append(dict(
                id=r["id"], no=r["invoice_no"], ref=r["client_ref"] or "", type=r["sale_type"], cust=r["customer_id"],
                cname=r["customer_name"] or "", cphone=r["customer_phone"] or "", d=r["business_date"],
                at=str(r["created_at"] or ""), gst_on=bool(r["gst_enabled"]), incl=bool(r["price_includes_tax"]),
                gross=r["gross_amount"], idisc=r["item_discount"], bdisc=r["bill_discount"],
                taxable=r["taxable_amount"], gst=r["gst_amount"], round=r["round_off"], total=r["grand_total"],
                paid=r["paid_amount"], returned=r["returned_amount"], refunded=r["refunded_amount"],
                due=due if r["status"] == "COMPLETED" else 0, st=r["status"], notes=r["notes"] or "",
                why=r["cancel_reason"] or "", blk=blk, items=items.get(r["id"], []), pays=plist))
        jobs = _rows(s, "SELECT * FROM service_jobs WHERE status NOT IN ('DELIVERED','CANCELLED') "
                        "OR received_date >= :cut OR delivered_date >= :cut ORDER BY id DESC", cut=cut)
        jids = [r["id"] for r in jobs]
        jitems: dict[int, list] = {}
        jpays: dict[int, list] = {}
        if jids:
            jl = ",".join(str(i) for i in jids)
            for r in _rows(s, "SELECT id, job_id, product_id, product_name, quantity, unit_price, line_amount, status "
                              f"FROM service_items WHERE job_id IN ({jl}) AND status='ACTIVE' ORDER BY id"):
                jitems.setdefault(r["job_id"], []).append(dict(id=r["id"], p=r["product_id"], name=r["product_name"],
                                                               q=r["quantity"], price=r["unit_price"],
                                                               amt=r["line_amount"]))
            for r in _rows(s, "SELECT ref_id, business_date, direction, mode, amount, kind FROM payments "
                              f"WHERE ref_type='SERVICE' AND status='ACTIVE' AND ref_id IN ({jl}) ORDER BY id"):
                jpays.setdefault(r["ref_id"], []).append(dict(d=r["business_date"], dir=r["direction"],
                                                              mode=r["mode"], amt=r["amount"], kind=r["kind"]))
        out["jobs"] = [dict(id=r["id"], no=r["job_no"], ref=r["client_ref"] or "", cust=r["customer_id"],
                            cname=r["customer_name"] or "", cphone=r["customer_phone"] or "", brand=r["brand"] or "",
                            model=r["model"] or "", imei=r["imei"] or "", acc=r["accessories"] or "",
                            problem=r["problem"] or "", diag=r["diagnosis"] or "", tech=r["technician"] or "",
                            d=r["received_date"], est=r["estimated_delivery"], st=r["status"],
                            labour=r["labour_charge"], disc=r["discount"], parts=r["parts_amount"],
                            total=r["total_amount"], paid=r["paid_amount"], done=r["completed_date"],
                            dlv=r["delivered_date"], wd=r["warranty_days"] or 0, notes=r["notes"] or "",
                            items=jitems.get(r["id"], []), pays=jpays.get(r["id"], []))
                       for r in jobs]
        out["expenses"] = [dict(id=r["id"], d=r["business_date"], cat=r["category"], desc=r["description"] or "",
                                amt=r["amount"], mode=r["payment_mode"])
                           for r in _rows(s, "SELECT id, business_date, category, description, amount, payment_mode "
                                             "FROM expenses WHERE status='ACTIVE' AND business_date >= :cut "
                                             "ORDER BY id DESC", cut=cut)]
        bdate = current_business_date(s)
    cp = cash_position(bdate, suggested_opening_cash(bdate))
    f = cp["f"]
    with session_scope() as s:
        today_sales = _rows(s, "SELECT COUNT(*) AS n, COALESCE(SUM(grand_total),0) AS t FROM sales "
                               "WHERE status='COMPLETED' AND business_date=:d", d=bdate.isoformat())[0]
        low = s.scalar(text("SELECT COUNT(*) FROM products WHERE is_active=1 AND quantity <= min_stock "
                            "AND min_stock > 0")) or 0
    open_jobs = sum(1 for j in out["jobs"] if j["st"] in ("RECEIVED", "INSPECTION", "WAITING_PARTS", "IN_PROGRESS"))
    out["dash"] = dict(d=bdate.isoformat(), bills=today_sales["n"], sales=today_sales["t"],
                       service=paise(f.get("service_income", 0)), expenses=paise(f.get("expenses", 0)),
                       opening=paise(cp["opening"]), cash=paise(cp["expected"]),
                       dues=sum(max(c["due"], 0) for c in out["customers"]), low=low, open_jobs=open_jobs,
                       ready=sum(1 for j in out["jobs"] if j["st"] == "READY"))
    return out


def _device_data(dev: SyncDevice) -> dict:
    """Settings, sign-in data and this phone's number series."""
    a = settings.get_all()
    with session_scope() as s:
        users = []
        for u in s.scalars(select(User).where(User.is_active.is_(True))):
            if u.id == dev.user_id or u.role in ("ADMIN", "MANAGER"):
                users.append(dict(id=u.id, username=u.username, name=u.full_name or u.username, role=u.role,
                                  perms=sorted(ROLE_PERMISSIONS.get(u.role, set())), hash=u.password_hash))
        walk_in = s.scalar(select(Customer.id).where(Customer.is_walk_in.is_(True)).order_by(Customer.id))
        inv_pre = f"{a.get('invoice_prefix') or 'SDJ'}-P{dev.device_no}"
        job_pre = f"{a.get('job_prefix') or 'JOB'}-P{dev.device_no}"
        fy = financial_year_label(date.today())
        last_inv = s.scalar(select(func.max(Sale.invoice_no)).where(Sale.invoice_no.like(f"{inv_pre}/{fy}/%"),
                                                                     ~Sale.invoice_no.like("%-E%"))) or ""
        last_job = s.scalar(select(func.max(ServiceJob.job_no)).where(ServiceJob.job_no.like(f"{job_pre}-%"))) or ""
        closed = sorted(d.isoformat() for d in closed_dates(s) if d >= date.today() - timedelta(days=400))
        bdate = current_business_date(s)
    keys = ("shop_name", "shop_tagline", "shop_address", "shop_phone", "shop_email", "shop_gstin", "invoice_footer",
            "terms", "service_terms", "upi_id", "invoice_prefix", "job_prefix", "gst_enabled", "prices_include_gst",
            "round_off", "service_gst_rate", "owner_approval", "staff_discount_limit", "low_stock_default",
            "default_repair_warranty_days")
    shop = {k: a.get(k) for k in keys}
    shop.update(expense_categories=list(a.get("expense_categories") or []),
                technicians=list(a.get("technicians") or []),
                walk_in=walk_in, inv_series=inv_pre, job_series=job_pre, fy=fy,
                inv_last=int(last_inv.rsplit("/", 1)[-1]) if last_inv else 0,
                job_last=int(last_job.rsplit("-", 1)[-1]) if last_job else 0,
                closed=closed, business_date=bdate.isoformat(), pc=pc_identity(), server_time=_now().isoformat(),
                device_no=dev.device_no, user_id=dev.user_id, history_days=int(a.get("phone_history_days") or 45),
                pc_version=APP_VERSION)
    return dict(shop=shop, users=users)


def _hash(obj) -> str:
    return hashlib.sha1(json.dumps(obj, separators=(",", ":"), sort_keys=True, default=str).encode()).hexdigest()[:16]


def build_data(dev: SyncDevice, token: str) -> dict:
    with _cache_lock:
        if _cache.get("token") != token:
            common = _common_data()
            _cache.clear()
            _cache.update(token=token, data=common, hashes={k: _hash(v) for k, v in common.items()})
        data = dict(_cache["data"])
        hashes = dict(_cache["hashes"])
    extra = _device_data(dev)
    data.update(extra)
    hashes.update({k: _hash(v) for k, v in extra.items()})
    return dict(data=data, hashes=hashes)


# ================================================================ the sync request
_sync_lock = threading.Lock()


def _device_for(env: dict) -> tuple[SyncDevice, bytes]:
    uid = str(env.get("d") or "")
    with session_scope() as s:
        dev = s.scalar(select(SyncDevice).where(SyncDevice.device_uid == uid))
        if dev is None:
            raise SyncError("This phone is not linked to this PC. Link it again from Settings → Phone link.",
                            "unknown", 401)
        if dev.status != "ACTIVE":
            raise SyncError("This phone was removed on the PC. Ask the owner to link it again.", "removed", 401)
        s.expunge(dev)
    return dev, _b64d(dev.secret)


_seen: dict[str, dict] = {}      # device uid -> last request (kept in memory; written to the database now and then)


def _note_request(dev: SyncDevice, seq: int, ip: str, app: str, force_write: bool) -> CurrentUser:
    """Replay check + "last seen". Writing this on every request would make the data look changed each time,
    so it is stored at most every 10 minutes (or together with real entries)."""
    mem = _seen.get(dev.device_uid) or {}
    if seq <= max(int(mem.get("seq") or 0), dev.last_seq):
        raise SyncError("Old or repeated request ignored.", "replay", 409)
    now = _now()
    mem.update(seq=seq, at=now, ip=ip, app=app)
    _seen[dev.device_uid] = mem
    write = force_write or not mem.get("written") or now - mem["written"] > timedelta(minutes=10)
    with session_scope() as s:
        d = s.get(SyncDevice, dev.id)
        u = s.get(User, d.user_id)
        if u is None or not u.is_active:
            raise SyncError("The user account of this phone is disabled on the PC.", "user_disabled", 403)
        if write:
            d.last_seq, d.last_seen_at, d.last_ip = seq, now, ip
            d.app_version = (app or d.app_version)[:20]
            mem["written"] = now
        return _as_cu(u)


def handle_sync(env: dict, ip: str = "") -> dict:
    dev, key = _device_for(env)
    aad = dev.device_uid.encode()
    req = unseal(key, env, aad)
    ops = list(req.get("ops") or [])[:MAX_OPS]
    acks = list(req.get("acks") or [])[:50]
    with _sync_lock:
        user = _note_request(dev, int(req.get("seq") or 0), ip, str(req.get("app") or ""), bool(ops))
        results = []
        if ops:
            _maybe_backup()
            batch = _Batch(dev)
            for op in ops:
                results.append(apply_op(dev, user, op, batch))
        backups = []
        try:
            from . import offsite
            with session_scope() as s:
                if acks:
                    offsite.mark_delivered(s, dev.id, acks)       # backups / reports the phone has saved
                backups = offsite.items_for_device(s, dev, user.role)
            epoch = offsite.data_epoch()
        except Exception:  # backups must never stop entries from syncing
            log.exception("phone backup list failed")
            epoch = settings.get("data_epoch") or ""
        token = data_token()
        answer = dict(seq=int(req.get("seq") or 0), results=results, token=token, time=_now().isoformat(),
                      backups=backups, epoch=epoch)
        update = _phone_update_for(str(req.get("app") or ""))
        if update:
            answer["update"] = update
        if req.get("token") != token or req.get("full"):
            built = build_data(dev, token)
            have = req.get("hashes") or {}
            answer["hashes"] = built["hashes"]
            answer["data"] = {k: v for k, v in built["data"].items()
                              if req.get("full") or have.get(k) != built["hashes"][k]}
    if results:
        saved = sum(1 for r in results if r["st"] == "DONE")
        problems = sum(1 for r in results if r["st"] != "DONE")
        _emit("synced", name=dev.name, saved=saved, problems=problems)
    else:
        _emit("seen", name=dev.name)
    return seal(key, answer, aad)


def _phone_update_for(app_version: str) -> dict | None:
    """The phone app copy this PC keeps, when it is newer than the phone's own version."""
    try:
        from . import updater
        apk = updater.staged_phone_apk()
        if apk and app_version and updater.is_newer(apk["version"], app_version):
            return dict(version=apk["version"], size=apk["size"], sha=apk["sha"], name=apk["name"],
                        notes=apk.get("notes", ""), date=apk.get("date", ""), recommended=bool(apk.get("recommended")))
    except Exception:
        log.exception("phone update offer failed")
    return None


def handle_apk(env: dict, ip: str = "") -> dict:
    """A linked phone downloads the newer phone app kept on this PC (a public, signed APK). Returns the file to stream."""
    from . import updater
    dev, key = _device_for(env)
    req = unseal(key, env, dev.device_uid.encode() + b"|apk")
    with _sync_lock:
        _note_request(dev, int(req.get("seq") or 0), ip, str(req.get("app") or ""), False)
    apk = updater.staged_phone_apk()
    if apk is None:
        raise SyncError("The PC has no newer phone app to give right now.", "no_update", 404)
    return dict(path=apk["path"], sha=apk["sha"], name=apk["name"])


def _maybe_backup() -> None:
    """A safety backup before the first phone entries of each hour are merged."""
    last = settings.get("phone_last_backup") or ""
    try:
        if last and datetime.now() - datetime.fromisoformat(last) < timedelta(hours=1):
            return
        from .backup import backup_now
        backup_now("before_phone_sync")
        settings.set_value("phone_last_backup", _now().isoformat())
    except Exception:  # a failed backup is logged; the entries are still saved safely in one transaction each
        log.exception("backup before phone sync failed")


def hello() -> dict:
    return dict(sdj=PROTOCOL, pc=pc_identity(), name=settings.get("shop_name") or "SDJ Mobile Care",
                port=PORT, version=APP_VERSION, restore=restore_code_info() is not None)


# ================================================================ backups to phones, restore to a (new) PC
RESTORE_MINUTES = 30
MAX_RESTORE_BYTES = 2 * 1024 ** 3
_restore: dict = {}            # the waiting restore code (memory only)


def handle_backup(env: dict, ip: str = "") -> dict:
    """A linked phone downloads one backup / closing report. Returns the file to stream (already encrypted)."""
    from . import offsite
    dev, key = _device_for(env)
    req = unseal(key, env, dev.device_uid.encode() + b"|backup")
    with _sync_lock:
        user = _note_request(dev, int(req.get("seq") or 0), ip, str(req.get("app") or ""), False)
    with session_scope() as s:
        try:
            item = offsite.item_for_device(s, dev, user.role, int(req.get("id") or 0))
        except NotFoundError as exc:
            raise SyncError(str(exc), "gone", 404) from exc
        except BusinessError as exc:
            raise SyncError(str(exc), "no_backups", 403) from exc
    return item


def new_restore_code() -> dict:
    """A code (and QR) the phone uses to send a backup – or its shop copy – to this PC. Valid 30 minutes."""
    from .auth import user_count
    if user_count() > 0:
        require("settings")
    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))
    with _codes_lock:
        _restore.clear()
        _restore.update(code=code, expires=_now() + timedelta(minutes=RESTORE_MINUTES), failures=0)
    _emit("restore_code")
    return restore_code_info()


def restore_code_info() -> dict | None:
    with _codes_lock:
        if not _restore:
            return None
        if _restore["expires"] <= _now():
            _restore.clear()
            return None
        code, expires = _restore["code"], _restore["expires"]
    ips = local_ips()
    name = (settings.get("shop_name") or "SDJ").replace(";", " ")
    qr = f"SDJRESTORE:{PROTOCOL};h={','.join(ips)};p={PORT};c={code};id={pc_identity()};n={name}"
    return dict(code=code, display=f"{code[:5]}-{code[5:]}", expires=expires, ips=ips, port=PORT, qr=qr)


def cancel_restore_code() -> None:
    with _codes_lock:
        _restore.clear()
    _emit("restore_code")


def _restore_open(env: dict, aad: bytes):
    with _codes_lock:
        live = dict(_restore) if _restore and _restore["expires"] > _now() else None
    if live is None:
        raise SyncError("No restore is waiting on this PC. On the PC choose 'Restore from phone' and scan the new QR.",
                        "no_restore", 403)
    pc_id = pc_identity()
    if env.get("pc") and env["pc"] != pc_id:
        raise SyncError("This code belongs to another computer.", "wrong_pc", 403)
    key = pair_key(live["code"], pc_id)
    try:
        return key, unseal(key, env, aad)
    except SyncError:
        with _codes_lock:
            if _restore:
                _restore["failures"] = _restore.get("failures", 0) + 1
                if _restore["failures"] >= 8:
                    _restore.clear()
        raise SyncError("The restore code is wrong or has expired. Scan the QR on the PC again.", "bad_code", 403)


def _clean_name(name: str) -> str:
    keep = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in (name or ""))[:120].strip("._")
    return keep or f"backup_{_now():%Y%m%d_%H%M%S}.sdjb"


def handle_restore_upload(auth: str, body, length: int, ip: str = "") -> dict:
    """A phone sends a backup file to this PC (raw body; the X-SDJ-Auth header proves it knows the restore code)."""
    from . import offsite
    try:
        env = json.loads(auth or "{}")
    except ValueError as exc:
        raise SyncError("Bad restore request.", "bad_request", 400) from exc
    key, meta = _restore_open(env, b"restore")
    size = int(meta.get("size") or 0)
    if size <= 0 or size != length or size > MAX_RESTORE_BYTES:
        raise SyncError("The file size does not match. Try again.", "size", 400)
    name = _clean_name(str(meta.get("name") or ""))
    dest = offsite.received_dir() / name
    tmp = dest.with_name(dest.name + ".part")
    h = hashlib.sha256()
    left = length
    with open(tmp, "wb") as f:
        while left > 0:
            chunk = body.read(min(1 << 20, left))
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            left -= len(chunk)
    if left or h.hexdigest() != str(meta.get("sha") or ""):
        tmp.unlink(missing_ok=True)
        raise SyncError("The file did not arrive complete. Try again on the shop Wi-Fi.", "incomplete", 400)
    os.replace(tmp, dest)
    try:
        info = offsite.describe_file(dest)
    except BusinessError as exc:
        dest.unlink(missing_ok=True)
        raise SyncError(str(exc), "not_backup", 400) from exc
    info = {k: v for k, v in info.items()}
    _emit("restore_file", path=str(dest), info=info, phone=str(meta.get("phone") or ""), ip=ip)
    with session_scope() as s:
        audit(s, "RESTORE_FILE_RECEIVED", "offsite", name, {"size": size, "phone": meta.get("phone"), "ip": ip})
    return seal(key, dict(ok=True, name=name, hello=meta.get("hello")), b"restore")


def handle_rebuild(env: dict, ip: str = "") -> dict:
    """Emergency: a phone sends its copy of the shop so a new PC can be rebuilt when there is no backup."""
    from . import offsite
    from .auth import user_count
    key, req = _restore_open(env, b"rebuild")
    if user_count() > 0:
        raise SyncError("This PC already has shop data. The emergency rebuild works only on a new PC (before setup).",
                        "not_empty", 409)
    try:
        summary = offsite.check_phone_copy(req)
    except BusinessError as exc:
        raise SyncError(str(exc), "bad_copy", 400) from exc
    path = offsite.received_dir() / f"phone_copy_{_now():%Y%m%d_%H%M%S}.json"
    path.write_text(json.dumps(req), encoding="utf-8")
    _emit("rebuild", path=str(path), summary=summary, phone=summary.get("phone") or "", ip=ip)
    return seal(key, dict(ok=True, hello=req.get("hello"), summary={k: str(v) if isinstance(v, Decimal) else v
                                                                   for k, v in summary.items()}), b"rebuild")


def sync_wanted() -> bool:
    """Run the network service only when a phone is linked or a link code is waiting."""
    if restore_code_info() is not None:
        return True
    if not settings.get("phone_sync_enabled", True):
        return False
    return active_device_count() > 0 or active_code() is not None


def last_seen_summary() -> str:
    devs = list_devices()
    if not devs:
        return "No phone linked yet"
    seen = [d["last_seen_at"] for d in devs if d["last_seen_at"]]
    if not seen:
        return f"{len(devs)} phone(s) linked · not synced yet"
    return f"{len(devs)} phone(s) linked · last sync {max(seen):%d %b %I:%M %p}"


def entries_summary(device_id: int | None = None, limit: int = 200) -> list[dict]:
    with session_scope() as s:
        q = select(SyncOp).order_by(SyncOp.id.desc()).limit(limit)
        if device_id:
            q = q.where(SyncOp.device_id == device_id)
        names = {d.id: d.name for d in s.scalars(select(SyncDevice))}
        return [dict(id=o.id, phone=names.get(o.device_id, ""), kind=KIND_LABELS.get(o.kind, o.kind),
                     made_at=o.made_at, received_at=o.received_at, status=o.status, message=o.message)
                for o in s.scalars(q)]


__all__ = ["PORT", "DISCOVERY_PORT", "SyncError", "new_link_code", "active_code", "cancel_link_code", "handle_pair",
           "handle_sync", "hello", "list_devices", "remove_device", "list_alerts", "resolve_alert",
           "use_phone_version", "update_closing_from_alert", "open_alert_count", "qr_matrix", "local_ips",
           "subscribe", "unsubscribe", "sync_wanted", "seal", "unseal", "pair_key", "pc_identity", "DailyClosing",
           "new_restore_code", "restore_code_info", "cancel_restore_code", "handle_backup", "handle_restore_upload",
           "handle_rebuild",
           "Expense", "Payment", "ServiceItem", "q2"]
