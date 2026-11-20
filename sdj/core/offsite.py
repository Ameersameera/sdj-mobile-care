"""Backups kept outside this PC: an encrypted bundle after every daily closing.

The bundle goes to the linked phones (over the shop Wi-Fi, with the next sync) and is copied to a 2nd backup folder
(pen drive, or a Google Drive for desktop folder). If the PC and its disk are lost, a new PC restores it from the
phone, the pen drive or a Google Drive download. With no backup at all, a new PC can be rebuilt from a phone's copy
(products, stock, IMEIs, customers with dues, open repair jobs).

Bundle file (.sdjb)::

    b"SDJB1\\n" | header length (4 bytes, big endian) | header JSON | nonce (12) | AES-256-GCM(zip)

The zip holds the database snapshot, the shop logo files, the day's closing report and a manifest. A random key K
encrypts the zip; K is "wrapped" once for every owner account (with a key made from that owner's password hash, so the
owner's password opens it) and once for the recovery code. The header is authenticated, so it cannot be changed.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import struct
import tempfile
import zipfile
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import delete, func, select

from .. import APP_VERSION, config
from ..db import base
from ..db.base import session_scope
from ..db.init_db import is_sdj_database, quick_check
from ..db.models import (Category, Counter, Customer, ImeiRecord, OffsiteDelivery, OffsiteItem, Product, ServiceItem,
                         ServiceJob, SyncDevice, User)
from . import settings
from .audit import audit
from .auth import SYSTEM_USER, check_own_password, require, user_count, verify_password
from .backup import backup_now, restore_backup
from .errors import BusinessError, NotFoundError, ValidationError

log = logging.getLogger(__name__)

MAGIC = b"SDJB1\n"
EXT = ".sdjb"
RC_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
RC_ITERATIONS = 200_000
KEEP_LOCAL = 15                      # bundles kept in the PC's outbox for phones that were away
PHONE_LATE_DAYS = 2
SECOND_LATE_DAYS = 7
OPEN_JOB = ("DELIVERED", "CANCELLED")


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def _rs(paise) -> Decimal:
    return (Decimal(int(paise or 0)) / 100).quantize(Decimal("0.01"))


def outbox_dir() -> Path:
    p = config.data_root() / "offsite"
    p.mkdir(parents=True, exist_ok=True)
    return p


def received_dir() -> Path:
    p = outbox_dir() / "received"
    p.mkdir(parents=True, exist_ok=True)
    return p


def data_epoch() -> str:
    """Changes when the data is restored, so phones re-send their recent entries (the PC saves each one only once)."""
    e = settings.get("data_epoch") or ""
    if not e:
        e = secrets.token_hex(6)
        settings.set_value("data_epoch", e)
    return e


# ================================================================ keys
def _kek_from_digest(digest: bytes) -> bytes:
    return hashlib.sha256(b"SDJ-BACKUP-PW" + digest).digest()


def _owner_wraps(s, key: bytes) -> list[dict]:
    """K wrapped for every active owner (ADMIN). Made from the stored password hash: no password is needed here."""
    out = []
    for u in s.scalars(select(User).where(User.is_active.is_(True), User.role == "ADMIN").order_by(User.id)):
        try:
            algo, it, salt, digest = u.password_hash.split("$")
            if algo != "pbkdf2_sha256":
                continue
            kek = _kek_from_digest(bytes.fromhex(digest))
        except ValueError:
            continue
        nonce = os.urandom(12)
        wrapped = AESGCM(kek).encrypt(nonce, key, b"SDJB-key")
        if AESGCM(kek).decrypt(nonce, wrapped, b"SDJB-key") != key:        # checked before it is relied on
            raise BusinessError("Backup key check failed.")
        out.append(dict(t="pw", u=u.username, it=int(it), salt=salt, n=_b64e(nonce), w=_b64e(wrapped)))
    return out


def normalise_code(code: str) -> str:
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


def _rc_wrap(key: bytes) -> dict | None:
    rc = settings.get("backup_rc") or {}
    if not rc.get("kek"):
        return None
    nonce = os.urandom(12)
    return dict(t="rc", it=int(rc["it"]), salt=rc["salt"], n=_b64e(nonce),
                w=_b64e(AESGCM(_b64d(rc["kek"])).encrypt(nonce, key, b"SDJB-key")))


def create_recovery_code(password: str) -> str:
    """A new recovery code (shown once). It opens every backup made from now on, even if the password is lost."""
    require("settings")
    check_own_password(password)
    raw = "".join(secrets.choice(RC_ALPHABET) for _ in range(24))
    salt = secrets.token_hex(16)
    kek = hashlib.pbkdf2_hmac("sha256", raw.encode(), bytes.fromhex(salt), RC_ITERATIONS, 32)
    settings.set_value("backup_rc", {"salt": salt, "it": RC_ITERATIONS, "kek": _b64e(kek), "created": _now().isoformat()})
    with session_scope() as s:
        audit(s, "RECOVERY_CODE_CREATED", "settings", "backup_rc", {})
    return "-".join(raw[i:i + 4] for i in range(0, 24, 4))


def recovery_code_created() -> str:
    return (settings.get("backup_rc") or {}).get("created") or ""


# ================================================================ making a bundle
def _snapshot_db() -> Path:
    src = base.current_db_path() or config.db_path()
    tmp = Path(tempfile.mkdtemp(prefix="sdj_snap_")) / "snapshot.db"
    with base.db_lock:
        a = sqlite3.connect(str(src), timeout=15)
        b = sqlite3.connect(str(tmp))
        try:
            a.backup(b)
        finally:
            b.close()
            a.close()
    ok, msg = quick_check(tmp)
    if not ok:
        shutil.rmtree(tmp.parent, ignore_errors=True)
        raise BusinessError(f"Backup check failed: {msg}")
    return tmp


def _counts(db: Path) -> dict:
    con = sqlite3.connect(str(db))
    try:
        out = {}
        for t in ("products", "customers", "sales", "service_jobs", "expenses"):
            try:
                out[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.Error:
                out[t] = 0
        return out
    finally:
        con.close()


def _parse(raw: bytes) -> tuple[dict, bytes, bytes]:
    if not raw.startswith(MAGIC) or len(raw) < len(MAGIC) + 4:
        raise ValidationError("This is not an SDJ Mobile Care backup file (.sdjb).")
    (n,) = struct.unpack(">I", raw[len(MAGIC):len(MAGIC) + 4])
    if n <= 0 or n > 1_000_000 or len(raw) < len(MAGIC) + 4 + n + 28:
        raise ValidationError("The backup file is damaged (incomplete).")
    hb = raw[len(MAGIC) + 4:len(MAGIC) + 4 + n]
    try:
        header = json.loads(hb.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("The backup file is damaged.") from exc
    return header, hb, raw[len(MAGIC) + 4 + n:]


def _unzip_checked(data: bytes) -> Path:
    out = Path(tempfile.mkdtemp(prefix="sdj_restore_"))
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if z.testzip() is not None:
                raise ValidationError("The backup file is damaged (zip check failed).")
            for member in z.namelist():
                target = (out / member).resolve()
                if out.resolve() not in target.parents:          # never write outside the folder
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(member))
        ok, msg = is_sdj_database(out / "sdj_mobile_care.db")
        if not ok:
            raise ValidationError(f"The backup does not contain a usable shop database ({msg}).")
        return out
    except (zipfile.BadZipFile, OSError) as exc:
        shutil.rmtree(out, ignore_errors=True)
        raise ValidationError(f"The backup file is damaged ({exc}).") from exc
    except ValidationError:
        shutil.rmtree(out, ignore_errors=True)
        raise


def build_bundle(dest: Path, *, label: str, business_date: date | None, pdf_path: str | None = None) -> dict:
    db = _snapshot_db()
    try:
        manifest = dict(app=APP_VERSION, created=_now().isoformat(), label=label,
                        business_date=business_date.isoformat() if business_date else None,
                        shop=settings.get("shop_name") or "", counts=_counts(db))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            z.write(db, "sdj_mobile_care.db")
            z.writestr("manifest.json", json.dumps(manifest, indent=1))
            logo_dir = config.logo_store_dir()
            for f in (sorted(logo_dir.glob("*")) if logo_dir.exists() else []):
                if f.is_file():
                    z.write(f, f"branding/{f.name}")
            if pdf_path and Path(pdf_path).exists():
                z.write(pdf_path, "closing_report.pdf")
        data = buf.getvalue()
    finally:
        shutil.rmtree(db.parent, ignore_errors=True)
    key = os.urandom(32)
    with session_scope() as s:
        wraps = _owner_wraps(s, key)
    if not wraps:
        raise BusinessError("No owner account found to protect the backup.")
    rc = _rc_wrap(key)
    if rc:
        wraps.append(rc)
    header = dict(v=1, created=manifest["created"], label=label, date=manifest["business_date"], shop=manifest["shop"],
                  app=APP_VERSION, counts=manifest["counts"], keys=wraps)
    hb = json.dumps(header, separators=(",", ":")).encode("utf-8")
    nonce = os.urandom(12)
    body = AESGCM(key).encrypt(nonce, data, MAGIC + hb)
    blob = MAGIC + struct.pack(">I", len(hb)) + hb + nonce + body
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(blob)
    # auto-check: read the file back, decrypt it and check the database inside before it is used
    try:
        h2, hb2, rest = _parse(tmp.read_bytes())
        folder = _unzip_checked(AESGCM(key).decrypt(rest[:12], rest[12:], MAGIC + hb2))
    except InvalidTag as exc:
        tmp.unlink(missing_ok=True)
        raise BusinessError("The new backup failed its check (could not be read back).") from exc
    except ValidationError:
        tmp.unlink(missing_ok=True)
        raise
    shutil.rmtree(folder, ignore_errors=True)
    os.replace(tmp, dest)
    return dict(path=dest, size=len(blob), sha256=hashlib.sha256(blob).hexdigest(), header=header)


def _safe(text: str) -> str:
    return re.sub(r"_+", "_", "".join(ch if ch.isalnum() else "_" for ch in (text or "SDJ")))[:30].strip("_") or "SDJ"


def _lead() -> str:
    shop = _safe(settings.get("shop_name"))
    return shop if shop.upper().startswith("SDJ") else f"SDJ_{shop}"


def create_bundle(label: str = "closing", business_date: date | None = None, pdf_path: str | None = None) -> dict:
    """Makes the encrypted bundle (and the closing report item), copies it to the 2nd folder, keeps it for phones."""
    when = _now()
    day = business_date or when.date()
    name = f"{_lead()}_{day:%Y-%m-%d}_{when:%H%M%S}_{label}{EXT}"
    n = 2
    while (outbox_dir() / name).exists():                 # two backups in the same second
        name = f"{_lead()}_{day:%Y-%m-%d}_{when:%H%M%S}_{label}_{n}{EXT}"
        n += 1
    try:
        info = build_bundle(outbox_dir() / name, label=label, business_date=business_date, pdf_path=pdf_path)
    except Exception as exc:
        settings.set_value("offsite_last_error", f"{when:%d %b %I:%M %p}: {exc}")
        raise
    with session_scope() as s:
        item = OffsiteItem(kind="BACKUP", name=name, size=info["size"], sha256=info["sha256"], label=label,
                           business_date=day)
        s.add(item)
        if pdf_path and Path(pdf_path).exists():
            rname = f"{_lead()}_Closing_{day:%Y-%m-%d}.pdf"
            shutil.copy2(pdf_path, outbox_dir() / rname)
            raw = (outbox_dir() / rname).read_bytes()
            old = s.scalar(select(OffsiteItem).where(OffsiteItem.kind == "REPORT", OffsiteItem.name == rname))
            if old is not None:                     # day reopened and closed again: send the new report
                old.name = f"replaced_{old.id}_{rname}"
            s.add(OffsiteItem(kind="REPORT", name=rname, size=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                              label="closing", business_date=day))
        s.flush()
        item_id = item.id
        audit(s, "OFFSITE_BACKUP_CREATED", "offsite_item", item_id, {"name": name, "size": info["size"]})
    settings.set_many({"offsite_last_bundle": when.isoformat(), "offsite_last_error": ""})
    copy_to_second_folder()
    _cleanup_outbox()
    return dict(id=item_id, name=name, size=info["size"], path=str(info["path"]))


def after_closing(d: date, pdf_path: str | None) -> dict | None:
    """Called by the daily closing. Never stops the closing if something goes wrong."""
    try:
        return create_bundle("closing", d, pdf_path)
    except Exception:
        log.exception("off-PC backup after closing failed")
        return None


def _cleanup_outbox() -> None:
    with session_scope() as s:
        keep = set()
        for kind in ("BACKUP", "REPORT"):
            keep |= {i.name for i in s.scalars(select(OffsiteItem).where(OffsiteItem.kind == kind)
                                               .order_by(OffsiteItem.id.desc()).limit(KEEP_LOCAL))}
    for f in outbox_dir().glob("*"):
        if f.is_file() and f.name not in keep:
            f.unlink(missing_ok=True)


def item_path(item_id: int) -> Path:
    with session_scope() as s:
        it = s.get(OffsiteItem, item_id)
        if it is None:
            raise NotFoundError("Backup not found.")
        p = outbox_dir() / it.name
    if not p.exists():
        raise NotFoundError("This backup file is no longer on the PC.")
    return p


# ================================================================ opening / restoring
def is_bundle(path: str | Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def read_header(path: str | Path) -> dict:
    with open(path, "rb") as f:
        head = f.read(len(MAGIC) + 4)
        if not head.startswith(MAGIC) or len(head) < len(MAGIC) + 4:
            raise ValidationError("This is not an SDJ Mobile Care backup file (.sdjb).")
        (n,) = struct.unpack(">I", head[len(MAGIC):])
        if n <= 0 or n > 1_000_000:
            raise ValidationError("The backup file is damaged.")
        try:
            return json.loads(f.read(n).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError("The backup file is damaged.") from exc


def describe_file(path: str | Path) -> dict:
    """What a received / chosen backup file is, for the restore screen."""
    p = Path(path)
    if is_bundle(p):
        h = read_header(p)
        return dict(kind="bundle", name=p.name, size=p.stat().st_size, created=h.get("created") or "",
                    date=h.get("date") or "", shop=h.get("shop") or "", label=h.get("label") or "",
                    app=h.get("app") or "", counts=h.get("counts") or {},
                    owners=[k.get("u") for k in h.get("keys", []) if k.get("t") == "pw"],
                    recovery=any(k.get("t") == "rc" for k in h.get("keys", [])))
    ok, msg = is_sdj_database(p)
    if not ok:
        raise ValidationError(f"This is not an SDJ Mobile Care backup ({msg}).")
    return dict(kind="db", name=p.name, size=p.stat().st_size, created="", date="", shop="", label="", app="",
                counts=_counts(p), owners=[], recovery=False)


def _unwrap(header: dict, secret: str) -> bytes:
    secret = (secret or "").strip()
    if not secret:
        raise ValidationError("Enter the owner password or the recovery code.")
    code = normalise_code(secret)
    for k in header.get("keys", []):
        try:
            if k["t"] == "pw":
                digest = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), bytes.fromhex(k["salt"]), int(k["it"]))
                kek = _kek_from_digest(digest)
            elif k["t"] == "rc":
                if len(code) != 24:
                    continue
                kek = hashlib.pbkdf2_hmac("sha256", code.encode(), bytes.fromhex(k["salt"]), int(k["it"]), 32)
            else:
                continue
            return AESGCM(kek).decrypt(_b64d(k["n"]), _b64d(k["w"]), b"SDJB-key")
        except (InvalidTag, ValueError, KeyError, TypeError):
            continue
    raise ValidationError("The password or recovery code is not correct for this backup. Use the owner password that "
                          "was in use when the backup was made, or the recovery code.")


def open_bundle(path: str | Path, secret: str) -> Path:
    """Decrypts a bundle into a temporary folder; returns that folder (database, branding, report, manifest)."""
    header, hb, rest = _parse(Path(path).read_bytes())
    key = _unwrap(header, secret)
    try:
        data = AESGCM(key).decrypt(rest[:12], rest[12:], MAGIC + hb)
    except InvalidTag as exc:
        raise ValidationError("The backup file is damaged or was changed.") from exc
    return _unzip_checked(data)


def check_bundle(path: str | Path, secret: str) -> dict:
    """Opens the bundle fully (password, decryption, database check) without restoring it."""
    folder = open_bundle(path, secret)
    try:
        return describe_file(path) | dict(ok=True, counts=_counts(folder / "sdj_mobile_care.db"))
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def restore_bundle(path: str | Path, secret: str) -> dict:
    """Replaces this PC's data with the backup (a safety copy of the current data is made first)."""
    folder = open_bundle(path, secret)
    try:
        header = read_header(path)
        safety = restore_backup(folder / "sdj_mobile_care.db")
        brand = folder / "branding"
        if brand.exists():
            dest = config.logo_store_dir()
            dest.mkdir(parents=True, exist_ok=True)
            for f in brand.glob("*"):
                shutil.copy2(f, dest / f.name)
            logo = settings.get("shop_logo_path") or ""
            if logo and (dest / Path(logo).name).exists() and Path(logo) != dest / Path(logo).name:
                settings.set_value("shop_logo_path", str(dest / Path(logo).name))   # this PC's own folder
        with session_scope() as s:
            audit(s, "BACKUP_RESTORED", "offsite", Path(path).name, {"created": header.get("created"),
                                                                     "label": header.get("label")}, user=SYSTEM_USER)
        return dict(created=header.get("created"), date=header.get("date"), safety=str(safety),
                    counts=header.get("counts") or {})
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def restore_any(path: str | Path, secret: str = "") -> dict:
    """Restore from a .sdjb bundle (needs the owner password / recovery code) or a plain .db backup."""
    p = Path(path)
    if is_bundle(p):
        return restore_bundle(p, secret)
    safety = restore_backup(p)
    return dict(created=None, date=None, safety=str(safety), counts={})


# ================================================================ 2nd backup folder (pen drive / Google Drive folder)
def second_folder() -> str:
    return (settings.get("backup_dir2") or "").strip()


def set_second_folder(path: str) -> None:
    require("settings")
    path = (path or "").strip()
    if path:
        p = Path(path)
        try:
            p.mkdir(parents=True, exist_ok=True)
            probe = p / ".sdj_write_test"
            probe.write_text("ok")
            probe.unlink()
        except OSError as exc:
            raise ValidationError(f"Cannot write to this folder: {exc}") from exc
    settings.set_many({"backup_dir2": path, "offsite_second_last": "", "offsite_second_error": "",
                       "offsite_second_name": ""})
    with session_scope() as s:
        audit(s, "BACKUP_FOLDER2_SET", "settings", "backup_dir2", {"path": path})
    if path:
        copy_to_second_folder()


def _stamp(name: str) -> str:
    m = re.search(r"_(\d{4}-\d{2}-\d{2})_(\d{6})_", name)
    return (m.group(1) + m.group(2)) if m else ""


def keep_names(names: list[str], keep_daily: int = 30) -> set[str]:
    """Newest *keep_daily* bundles plus the newest one of every month (kept forever)."""
    ordered = sorted(names, key=lambda n: (_stamp(n), n), reverse=True)
    keep = set(ordered[:keep_daily])
    months: set[str] = set()
    for n in ordered:
        month = _stamp(n)[:7]
        if month and month not in months:
            months.add(month)
            keep.add(n)
    return keep


def copy_to_second_folder() -> tuple[bool, str]:
    folder = second_folder()
    if not folder:
        return False, "No 2nd backup folder chosen."
    with session_scope() as s:
        latest = s.scalar(select(OffsiteItem).where(OffsiteItem.kind == "BACKUP").order_by(OffsiteItem.id.desc()))
        name = latest.name if latest else None
        report = s.scalar(select(OffsiteItem).where(OffsiteItem.kind == "REPORT").order_by(OffsiteItem.id.desc()))
        rname = report.name if report else None
    if not name or not (outbox_dir() / name).exists():
        return False, "No backup made yet."
    dest = Path(folder)
    try:
        if not dest.exists():
            raise OSError("folder not found (pen drive not connected?)")
        for n in (name, rname):
            if not n or not (outbox_dir() / n).exists():
                continue
            sub = dest if n == name else dest / "Reports"
            sub.mkdir(parents=True, exist_ok=True)
            if not (sub / n).exists():
                shutil.copy2(outbox_dir() / n, sub / (n + ".part"))
                os.replace(sub / (n + ".part"), sub / n)
        existing = [f.name for f in dest.glob(f"*{EXT}") if _stamp(f.name)]
        keep = keep_names(existing, int(settings.get("backup_keep") or 30))
        for f in existing:
            if f not in keep:
                (dest / f).unlink(missing_ok=True)
        settings.set_many({"offsite_second_last": _now().isoformat(), "offsite_second_error": "",
                           "offsite_second_name": name})
        return True, f"Copied {name}"
    except OSError as exc:
        settings.set_value("offsite_second_error", str(exc))
        log.warning("2nd backup folder copy failed: %s", exc)
        return False, f"Could not copy to the 2nd backup folder: {exc}"


def catch_up_second_folder() -> None:
    """If the pen drive was away at closing time, copy the latest backup when it is back (called now and then)."""
    try:
        if not second_folder():
            return
        with session_scope() as s:
            latest = s.scalar(select(OffsiteItem.name).where(OffsiteItem.kind == "BACKUP")
                              .order_by(OffsiteItem.id.desc()))
        if latest and settings.get("offsite_second_name") != latest and Path(second_folder()).exists():
            copy_to_second_folder()
    except Exception:  # never disturb the app for this
        log.exception("2nd folder catch-up failed")


# ================================================================ phones
def device_keeps_backups(dev: SyncDevice, role: str) -> bool:
    return bool(dev.backups) if dev.backups is not None else role == "ADMIN"


def set_device_backups(device_id: int, on: bool) -> None:
    require("settings")
    with session_scope() as s:
        d = s.get(SyncDevice, device_id)
        if d is None:
            raise NotFoundError("Phone not found.")
        d.backups = bool(on)
        audit(s, "PHONE_BACKUPS_SET", "sync_device", d.id, {"on": bool(on), "name": d.name})


def _item_dict(r: OffsiteItem) -> dict:
    return dict(id=r.id, kind=r.kind, name=r.name, size=r.size, sha=r.sha256,
                date=r.business_date.isoformat() if r.business_date else None,
                created=r.created_at.isoformat() if r.created_at else None, label=r.label)


def items_for_device(s, dev: SyncDevice, role: str) -> list[dict]:
    """What this phone should download: the newest backup (if it does not have it) and the closing reports since
    it was linked. A newly linked phone gets the newest backup and report only."""
    if not device_keeps_backups(dev, role):
        return []
    have = {r for (r,) in s.execute(select(OffsiteDelivery.item_id).where(OffsiteDelivery.device_id == dev.id))}
    out = []
    newest = s.scalar(select(OffsiteItem).where(OffsiteItem.kind == "BACKUP").order_by(OffsiteItem.id.desc()))
    if newest is not None and newest.id not in have:
        out.append(newest)
    reports = list(s.scalars(select(OffsiteItem).where(OffsiteItem.kind == "REPORT",
                                                       ~OffsiteItem.name.like("replaced_%"))
                             .order_by(OffsiteItem.id.desc()).limit(KEEP_LOCAL)))
    if not have:
        reports = reports[:1]
    else:
        first = min(have)
        reports = [r for r in reports if r.id not in have and r.id > first]
    out += reports
    return [_item_dict(r) for r in sorted(out, key=lambda x: x.id) if (outbox_dir() / r.name).exists()][:12]


def mark_delivered(s, device_id: int, shas) -> int:
    """The phone lists the SHA-256 of the backups / reports it has saved (numbers would not survive a restore of the
    PC: a new PC numbers its backups again from where the restored data left off)."""
    want = {str(x).lower() for x in (shas or []) if isinstance(x, str) and len(x) == 64}
    if not want:
        return 0
    n = 0
    for item in s.scalars(select(OffsiteItem).where(OffsiteItem.sha256.in_(want))):
        exists = s.scalar(select(OffsiteDelivery.id).where(OffsiteDelivery.item_id == item.id,
                                                           OffsiteDelivery.device_id == device_id))
        if exists is None:
            s.add(OffsiteDelivery(item_id=item.id, device_id=device_id))
            n += 1
    return n


def item_for_device(s, dev: SyncDevice, role: str, item_id: int) -> dict:
    if not device_keeps_backups(dev, role):
        raise BusinessError("This phone is not set to keep backups. Turn it on in Settings → Phone link on the PC.")
    it = s.get(OffsiteItem, item_id)
    if it is None or not (outbox_dir() / it.name).exists():
        raise NotFoundError("This backup is no longer on the PC.")
    return _item_dict(it) | dict(path=outbox_dir() / it.name)


# ================================================================ status (dashboard / Settings → Backup)
def status() -> dict:
    with session_scope() as s:
        latest = s.scalar(select(OffsiteItem).where(OffsiteItem.kind == "BACKUP").order_by(OffsiteItem.id.desc()))
        latest_d = dict(name=latest.name, created=latest.created_at, size=latest.size) if latest else None
        phones = []
        for d in s.scalars(select(SyncDevice).where(SyncDevice.status == "ACTIVE").order_by(SyncDevice.id)):
            u = s.get(User, d.user_id)
            last = s.scalar(select(func.max(OffsiteDelivery.delivered_at))
                            .join(OffsiteItem, OffsiteItem.id == OffsiteDelivery.item_id)
                            .where(OffsiteDelivery.device_id == d.id, OffsiteItem.kind == "BACKUP"))
            phones.append(dict(id=d.id, name=d.name, keeps=device_keeps_backups(d, u.role if u else ""), last=last))
    now = _now()
    warnings = []
    keeping = [p for p in phones if p["keeps"]]
    phone_last = max((p["last"] for p in keeping if p["last"]), default=None)
    if latest_d is not None and keeping and (phone_last is None or now - phone_last > timedelta(days=PHONE_LATE_DAYS)):
        if latest_d["created"] and now - latest_d["created"] > timedelta(hours=1):
            warnings.append("No backup has reached a phone for over 2 days – open the SDJ phone app on the shop Wi-Fi.")
    folder = second_folder()
    second_last = settings.get("offsite_second_last") or ""
    try:
        second_dt = datetime.fromisoformat(second_last) if second_last else None
    except ValueError:
        second_dt = None
    if folder and latest_d is not None and (second_dt is None or now - second_dt > timedelta(days=SECOND_LATE_DAYS)):
        warnings.append("The 2nd backup folder was not updated for over 7 days – connect the pen drive "
                        "(or check Google Drive for desktop).")
    late_flag = bool(warnings)
    if latest_d is not None and not recovery_code_created():
        warnings.append("No recovery code yet – create one so a backup can be opened even if the password is lost.")
    err = settings.get("offsite_last_error") or ""
    if err:
        warnings.insert(0, f"The last off-PC backup failed: {err}")
        late_flag = True
    return dict(latest=latest_d, phones=phones, phone_last=phone_last, second_folder=folder, second_last=second_dt,
                second_error=settings.get("offsite_second_error") or "", recovery=recovery_code_created(),
                warnings=warnings, late=late_flag)


def late() -> bool:
    try:
        return status()["late"]
    except Exception:  # never break the header
        return False


# ================================================================ emergency: rebuild a new PC from a phone's copy
SHOP_KEYS = ("shop_name", "shop_tagline", "shop_address", "shop_phone", "shop_email", "shop_gstin", "invoice_footer",
             "terms", "service_terms", "upi_id", "invoice_prefix", "job_prefix", "gst_enabled", "prices_include_gst",
             "round_off", "service_gst_rate", "owner_approval", "staff_discount_limit", "low_stock_default",
             "default_repair_warranty_days")


def check_phone_copy(req: dict) -> dict:
    data = req.get("data") or {}
    shop = data.get("shop") or {}
    users = data.get("users") or []
    if not shop.get("pc") or not users or not (data.get("products") or data.get("customers")):
        raise ValidationError("The phone's copy is incomplete. The phone must have synced with the old PC at least once.")
    if not any(u.get("role") == "ADMIN" and u.get("hash") for u in users):
        raise ValidationError("The phone's copy has no owner login, so it cannot be used to rebuild the PC.")
    jobs = [j for j in data.get("jobs") or [] if j.get("st") not in OPEN_JOB]
    products = data.get("products") or []
    customers = data.get("customers") or []
    return dict(shop=shop.get("shop_name") or "", products=len(products),
                stock=sum(max(int(p.get("qty") or 0), 0) for p in products), imeis=len(data.get("imeis") or []),
                customers=len(customers), dues=_rs(sum(max(int(c.get("due") or 0), 0) for c in customers)),
                open_jobs=len(jobs), users=[u.get("username") for u in users], made=str(req.get("made") or ""),
                phone=str((req.get("device") or {}).get("name") or ""), last_sync=str(req.get("last_sync") or ""))


def _job_effect(j: dict) -> int:
    """How an open job counts in the customer's balance (paise): advance as credit, finished work as due."""
    if j.get("done"):
        return int(j.get("total") or 0) - int(j.get("paid") or 0)
    return -int(j.get("paid") or 0)


def _d(v) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def _set_counter(s, name: str, value: int) -> None:
    if value <= 0:
        return
    row = s.get(Counter, name)
    if row is None:
        s.add(Counter(name=name, value=value))
    elif (row.value or 0) < value:
        row.value = value


def rebuild_from_phone(req: dict, password: str) -> dict:
    """Creates the shop on this new PC from a phone's copy. Only allowed before any user exists on this PC."""
    from .inventory import apply_movement
    from .repairs import recalc_job
    if user_count() > 0:
        raise BusinessError("This PC already has shop data. The emergency rebuild works only on a new PC (before setup).")
    summary = check_phone_copy(req)
    data = req["data"]
    shop, users = data["shop"], data["users"]
    if not any(u.get("role") == "ADMIN" and verify_password(password or "", u.get("hash") or "") for u in users):
        raise ValidationError("Enter the owner's password – the same one used on the old PC.")
    today = date.today()
    notes: list[str] = []
    products = data.get("products") or []
    imeis = data.get("imeis") or []
    customers = data.get("customers") or []
    jobs = [j for j in data.get("jobs") or [] if j.get("st") not in OPEN_JOB]
    dev = req.get("device") or {}
    prefix = shop.get("invoice_prefix") or "SDJ"
    job_prefix = shop.get("job_prefix") or "JOB"
    # ---- settings first (only what the owner had entered on the old PC – nothing is invented), so GST rules
    #      below are worked out with the shop's own settings
    vals = {k: shop[k] for k in SHOP_KEYS if k in shop and shop[k] is not None}
    vals.update(phone_pc_id=shop["pc"], data_epoch=str(req.get("epoch") or "") or secrets.token_hex(6),
                rebuilt_from_phone=_now().isoformat(),
                expense_categories=list(shop.get("expense_categories") or settings.get("expense_categories") or []),
                technicians=list(shop.get("technicians") or []),
                phone_history_days=int(shop.get("history_days") or 45))
    settings.set_many(vals)
    with session_scope() as s:
        # ---- users (same logins and passwords as before)
        owner_id = None
        for u in users:
            if not u.get("username") or not u.get("hash"):
                continue
            s.add(User(id=int(u["id"]), username=u["username"], full_name=u.get("name") or "",
                       password_hash=u["hash"], role=u.get("role") or "CASHIER", is_active=True))
            if u.get("role") == "ADMIN" and owner_id is None:
                owner_id = int(u["id"])
        s.flush()
        # ---- categories (same numbers, so the phone's waiting entries still match)
        cats = data.get("categories") or []
        if cats:
            s.execute(delete(Category))
            for i, c in enumerate(cats):
                s.add(Category(id=int(c["id"]), name=str(c.get("name") or f"Category {c['id']}")[:60],
                               group=c.get("grp") or "ACCESSORY", requires_imei=bool(c.get("imei")), sort_order=i,
                               is_active=bool(c.get("on", True))))
            s.flush()
        cat_ids = [c.id for c in s.scalars(select(Category).order_by(Category.sort_order))]
        # ---- customers: old dues become the opening balance (open jobs are added back as jobs below)
        s.execute(delete(Customer))
        walk_in = int(shop.get("walk_in") or 0) or None
        wrow = Customer(id=walk_in, name="Walk-in Customer", phone="", is_walk_in=True)
        s.add(wrow)
        s.flush()
        walk_in = wrow.id
        effect: dict[int, int] = {}
        for j in jobs:
            effect[int(j.get("cust") or 0)] = effect.get(int(j.get("cust") or 0), 0) + _job_effect(j)
        cust_ids = {walk_in}
        credit_total = 0
        for c in customers:
            cid = int(c["id"])
            if cid in cust_ids:
                continue
            ob = int(c.get("due") or 0) - effect.get(cid, 0)
            note = ""
            if ob < 0:
                credit_total += -ob
                note = f"Credit (advance) of ₹{_rs(-ob)} on the old PC – check before giving it back."
                ob = 0
            s.add(Customer(id=cid, name=str(c.get("name") or "Customer")[:100], phone=str(c.get("phone") or "")[:20],
                           address=c.get("addr") or "", opening_balance=_rs(ob), notes=note))
            cust_ids.add(cid)
        for j in jobs:                          # a job's customer may be hidden (not in the phone's list)
            cid = int(j.get("cust") or 0)
            if cid and cid not in cust_ids:
                s.add(Customer(id=cid, name=str(j.get("cname") or "Customer")[:100],
                               phone=str(j.get("cphone") or "")[:20]))
                cust_ids.add(cid)
        if credit_total:
            notes.append(f"Customer credits of ₹{_rs(credit_total)} were noted on the customers (not as dues).")
        s.flush()
        # ---- products, stock and IMEIs (cost prices are not on the phone: set them later)
        by_product: dict[int, list] = {}
        for i in imeis:
            by_product.setdefault(int(i.get("p") or 0), []).append(i)
        stock_note = "Stock from the phone's copy (emergency rebuild)"
        pids = set()
        for p in products:
            cat = int(p.get("cat") or 0)
            if cat not in cat_ids:
                cat = cat_ids[0]
            row = Product(id=int(p["id"]), code=str(p.get("code") or f"P{int(p['id']):05d}"),
                          name=str(p.get("name") or "Product")[:120], brand=p.get("brand") or "", category_id=cat,
                          model_number=p.get("model") or "", barcode=(p.get("bc") or None), purchase_price=0,
                          selling_price=_rs(p.get("price")), avg_cost=0, quantity=0, min_stock=int(p.get("min") or 0),
                          warranty_months=int(p.get("wm") or 0), gst_rate=_rs(p.get("gst")),
                          hsn_code=p.get("hsn") or "", track_imei=bool(p.get("imei")), is_active=bool(p.get("on", True)))
            s.add(row)
            s.flush()
            pids.add(row.id)
            units = by_product.get(row.id, [])
            for u in units:
                s.add(ImeiRecord(id=int(u["id"]), product_id=row.id, imei1=str(u.get("i1") or ""),
                                 imei2=str(u.get("i2") or ""), serial_no=str(u.get("sn") or ""), status="IN_STOCK",
                                 source="OPENING", purchase_date=today, purchase_cost=0, notes=stock_note))
            qty = len(units) if row.track_imei else int(p.get("qty") or 0)
            if row.track_imei and len(units) != int(p.get("qty") or 0):
                notes.append(f"{row.name}: stock set to {len(units)} (IMEIs in stock); the phone showed {p.get('qty')}.")
            if qty > 0:
                apply_movement(s, row, qty, "OPENING", business_date=today, ref_type="OPENING", ref_id=row.id,
                               ref_line_id=row.id, reference=row.code, unit_cost=0, notes=stock_note)
            elif qty < 0:
                notes.append(f"{row.name}: the phone showed stock {qty}; set to 0 – count it.")
        # ---- open repair jobs (with the advance already received, which is not counted as today's cash)
        for j in jobs:
            cid = int(j.get("cust") or 0) or walk_in
            paid = int(j.get("paid") or 0)
            job = ServiceJob(id=int(j["id"]), job_no=str(j.get("no")), client_ref=(j.get("ref") or None),
                             customer_id=cid, customer_name=j.get("cname") or "", customer_phone=j.get("cphone") or "",
                             brand=j.get("brand") or "", model=j.get("model") or "", imei=j.get("imei") or "",
                             accessories=j.get("acc") or "", problem=j.get("problem") or "",
                             diagnosis=j.get("diag") or "", technician=j.get("tech") or "",
                             received_date=_d(j.get("d")) or today, estimated_delivery=_d(j.get("est")),
                             status=j.get("st") or "RECEIVED", labour_charge=_rs(j.get("labour")),
                             discount=_rs(j.get("disc")), paid_amount=_rs(paid),
                             completed_date=_d(j.get("done")), warranty_days=int(j.get("wd") or 0),
                             notes=((j.get("notes") or "") + (f"\nAdvance ₹{_rs(paid)} was received before (old PC)."
                                                              if paid else "")).strip(),
                             created_by=owner_id)
            s.add(job)
            s.flush()
            for it in j.get("items") or []:
                pid = int(it.get("p") or 0)
                if pid not in pids:
                    notes.append(f"Job {job.job_no}: part '{it.get('name')}' skipped (product not on the phone).")
                    continue
                prod = s.get(Product, pid)
                job.items.append(ServiceItem(product_id=pid, product_name=it.get("name") or prod.name,
                                             quantity=int(it.get("q") or 1), unit_price=_rs(it.get("price")),
                                             unit_cost=0, line_amount=_rs(it.get("amt")), cost_total=0,
                                             gst_rate=prod.gst_rate, status="ACTIVE", added_date=job.received_date))
            s.flush()
            try:
                recalc_job(s, job)
            except BusinessError:
                pass
            job.total_amount = _rs(j.get("total"))          # keep exactly what the customer was told
        # ---- this phone stays linked (same key), and numbers continue after the old ones
        if dev.get("uid") and dev.get("key") and int(dev.get("user_id") or 0) in {int(u["id"]) for u in users}:
            s.add(SyncDevice(device_uid=str(dev["uid"]), device_no=int(dev.get("no") or 1),
                             name=str(dev.get("name") or "Phone")[:80], user_id=int(dev["user_id"]),
                             secret=str(dev["key"]), linked_by="Emergency rebuild", status="ACTIVE"))
        codes = [int(m.group(1)) for p in products if (m := re.fullmatch(r"P(\d+)", str(p.get("code") or "")))]
        _set_counter(s, "product_code", max(codes, default=0))
        jnos = [int(m.group(1)) for j in data.get("jobs") or []
                if (m := re.fullmatch(re.escape(job_prefix) + r"-(\d+)", str(j.get("no") or "")))]
        _set_counter(s, "job_no", max(jnos, default=0))
        inv: dict[str, int] = {}
        phones = [int(dev.get("no") or 0)]
        for sale in data.get("sales") or []:
            no = str(sale.get("no") or "")
            if m := re.fullmatch(re.escape(prefix) + r"/(\d{2}-\d{2})/(\d+)", no):
                inv[m.group(1)] = max(inv.get(m.group(1), 0), int(m.group(2)))
            if m := re.match(re.escape(prefix) + r"-P(\d+)/", no):
                phones.append(int(m.group(1)))
        for fy, n in inv.items():
            _set_counter(s, f"invoice_{fy}", n)
        _set_counter(s, "phone_device_no", max(phones))
        summary["notes"] = notes
        audit(s, "REBUILT_FROM_PHONE", "offsite", summary.get("phone") or "phone",
              {k: v for k, v in summary.items() if k != "users"}, user=SYSTEM_USER)
    settings.clear_cache()
    try:
        backup_now("after_rebuild")
    except Exception:
        log.exception("backup after rebuild failed")
    return summary


__all__ = ["create_bundle", "after_closing", "open_bundle", "check_bundle", "restore_bundle", "restore_any",
           "read_header", "describe_file", "is_bundle", "create_recovery_code", "recovery_code_created",
           "set_second_folder", "second_folder", "copy_to_second_folder", "catch_up_second_folder",
           "set_device_backups", "items_for_device", "item_for_device", "mark_delivered", "item_path", "status",
           "late", "outbox_dir", "received_dir", "keep_names", "check_phone_copy", "rebuild_from_phone",
           "data_epoch"]
