"""Check for updates (GitHub Releases), download them safely and install them.

PC: a new Setup.exe is downloaded, its SHA-256 is checked, a backup is saved, then the installer runs silently
over the old program (shop data lives in a separate folder and is never touched) and the app opens again.
Phone: the PC keeps a copy of the phone app (APK) that matches its own version; linked phones fetch it over the
shop Wi-Fi at their next sync (see phonesync.py). Nothing here needs the phone to have internet.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Callable

from .. import APP_VERSION, config
from . import settings

log = logging.getLogger("sdj.updater")

REPO = "Ameersameera/sdj-mobile-care"
API = f"https://api.github.com/repos/{REPO}/releases"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
# Downloads only ever come from these hosts (GitHub's own file servers).
_HOSTS = ("github.com", "api.github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com",
          "github-releases.githubusercontent.com")
_SETUP_RE = re.compile(r"^SDJMobileCare_Setup_(\d+(?:\.\d+){1,3})\.exe$")
_APK_RE = re.compile(r"^SDJMobileCare-Phone-(\d+(?:\.\d+){1,3})\.apk$")
_UA = {"User-Agent": f"SDJMobileCare/{APP_VERSION}", "Accept": "application/vnd.github+json"}


class UpdateError(Exception):
    pass


def parse_version(v: str) -> tuple[int, ...]:
    nums = [int(x) for x in re.findall(r"\d+", v or "")][:4]
    return tuple(nums + [0] * (4 - len(nums)))


def is_newer(candidate: str, current: str = APP_VERSION) -> bool:
    return parse_version(candidate) > parse_version(current)


def can_self_install() -> bool:
    """Only the installed Windows program can replace itself (not a source checkout, not Linux)."""
    return bool(getattr(sys, "frozen", False)) and sys.platform.startswith("win")


def updates_dir() -> Path:
    d = config.data_root() / "updates"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------- talking to GitHub
def _check_url(url: str) -> None:
    m = re.match(r"^https://([^/]+)/", url or "")
    if not m or not (m.group(1) in _HOSTS or m.group(1).endswith(".githubusercontent.com")):
        raise UpdateError("The update link is not from GitHub, so it was not used.")


def _get_json(url: str, timeout: float = 10):
    _check_url(url)
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 - https, host checked
        return json.loads(r.read().decode("utf-8"))


def _asset_sha(rel: dict, asset: dict) -> str:
    """SHA-256 of an asset: GitHub's own digest, else the release's SHA256SUMS.txt."""
    d = str(asset.get("digest") or "")
    if d.lower().startswith("sha256:") and len(d) == 71:
        return d[7:].lower()
    for a in rel.get("assets") or []:
        if a.get("name") == "SHA256SUMS.txt":
            try:
                _check_url(a["browser_download_url"])
                req = urllib.request.Request(a["browser_download_url"], headers=_UA)
                with urllib.request.urlopen(req, timeout=15) as r:   # noqa: S310
                    for line in r.read().decode("utf-8", "replace").splitlines():
                        parts = line.split()
                        if len(parts) >= 2 and parts[-1].lstrip("*") == asset.get("name") and len(parts[0]) == 64:
                            return parts[0].lower()
            except Exception:
                log.exception("could not read SHA256SUMS.txt")
    return ""


def section_notes(body: str, version: str, limit: int = 1800) -> str:
    """Only this version's part of the release notes ("### What's new in X" up to the next version or Download)."""
    body = (body or "").replace("\r\n", "\n").strip()
    lines = body.split("\n")
    start = None
    for i, line in enumerate(lines):
        if re.match(rf"^#{{2,4}}\s*What's new in {re.escape(version)}\b", line.strip()):
            start = i
            break
    if start is None:
        return body[:limit]
    out = []
    for line in lines[start + 1:]:
        t = line.strip()
        if t.startswith("#") and (re.match(r"^#{2,4}\s*What's new in ", t) or re.match(r"^#{2,4}\s*Download", t)):
            break
        out.append(line)
    return "\n".join(out).strip()[:limit]


def is_recommended(notes: str) -> bool:
    """A release marks an important update with the line 'Recommended: yes'."""
    return bool(re.search(r"(?im)^\W*recommended\s*:\s*yes\b", notes or ""))


def clean_notes(notes: str) -> str:
    """Notes as plain text for the popup: no markdown marks, no 'Recommended' marker line."""
    out = []
    for line in (notes or "").splitlines():
        if re.match(r"(?i)^\W*recommended\s*:", line):
            continue
        line = re.sub(r"^#{1,6}\s*", "", line).replace("**", "").replace("`", "")
        out.append(line.rstrip())
    return "\n".join(out).strip()


def _describe(rel: dict) -> dict:
    tag = str(rel.get("tag_name") or "")
    ver = tag.lstrip("vV")
    setup = apk = None
    for a in rel.get("assets") or []:
        n = str(a.get("name") or "")
        if _SETUP_RE.match(n):
            setup = a
        elif _APK_RE.match(n):
            apk = a
    notes = section_notes(str(rel.get("body") or ""), ver)
    return dict(
        version=ver, tag=tag, notes=clean_notes(notes), recommended=is_recommended(notes),
        date=str(rel.get("published_at") or "")[:10], page=str(rel.get("html_url") or RELEASES_PAGE),
        setup=None if setup is None else dict(name=setup["name"], url=setup["browser_download_url"],
                                              size=int(setup.get("size") or 0), sha=_asset_sha(rel, setup)),
        apk=None if apk is None else dict(name=apk["name"], url=apk["browser_download_url"],
                                          size=int(apk.get("size") or 0), sha=_asset_sha(rel, apk)),
    )


def fetch_latest() -> dict | None:
    """The newest published release, or None when it cannot be read. Never raises."""
    try:
        rel = _get_json(f"{API}/latest")
        if rel.get("draft") or rel.get("prerelease"):
            return None
        return _describe(rel)
    except Exception as exc:
        log.info("update check failed: %s", exc)
        return None


def check(*, record: bool = True) -> dict:
    """{state: 'new'|'current'|'offline', latest: {...}|None}. 'new' only when a newer, installable release exists."""
    from datetime import datetime
    info = fetch_latest()
    if info is None:
        return dict(state="offline", latest=None)
    if record:
        try:
            settings.set_value("update_last_check", datetime.now().isoformat(timespec="seconds"))
        except Exception:
            log.exception("could not record update check time")
    if is_newer(info["version"]) and info["setup"] is not None:
        return dict(state="new", latest=info)
    return dict(state="current", latest=info)


def should_check_now(hours: float = 12) -> bool:
    """Automatic checks: when switched on and not done in the last [hours]."""
    from datetime import datetime, timedelta
    if not settings.get("update_auto_check"):
        return False
    last = settings.get("update_last_check") or ""
    try:
        return not last or datetime.now() - datetime.fromisoformat(last) > timedelta(hours=hours)
    except ValueError:
        return True


# ---------------------------------------------------------------- downloading
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(asset: dict, progress: Callable[[int, int], bool | None] | None = None, dest_dir: Path | None = None) -> Path:
    """Download one release file, check its SHA-256, return its path. progress(done, total) may return False to stop."""
    if not asset or not asset.get("sha"):
        raise UpdateError("This release has no checksum, so it cannot be verified. Download it from the releases page instead.")
    _check_url(asset["url"])
    name = Path(str(asset["name"])).name
    if not (_SETUP_RE.match(name) or _APK_RE.match(name)):
        raise UpdateError("Unexpected file name in the release.")
    folder = dest_dir or updates_dir()
    folder.mkdir(parents=True, exist_ok=True)
    final = folder / name
    if final.exists() and sha256_file(final) == asset["sha"]:
        return final
    tmp = folder / (name + ".part")
    req = urllib.request.Request(asset["url"], headers={"User-Agent": _UA["User-Agent"]})
    try:
        with urllib.request.urlopen(req, timeout=30) as r, open(tmp, "wb") as out:   # noqa: S310
            _check_url(r.geturl())
            total = int(r.headers.get("Content-Length") or asset.get("size") or 0)
            done = 0
            while True:
                chunk = r.read(1 << 18)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if progress and progress(done, total) is False:
                    raise UpdateError("Cancelled.")
    except UpdateError:
        tmp.unlink(missing_ok=True)
        raise
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise UpdateError(f"Download failed: {exc}") from exc
    if sha256_file(tmp) != asset["sha"]:
        tmp.unlink(missing_ok=True)
        raise UpdateError("The downloaded file did not match its checksum, so it was thrown away. Please try again.")
    tmp.replace(final)
    for old in folder.glob("SDJMobileCare_Setup_*.exe"):          # keep only the newest installer
        if old != final and _SETUP_RE.match(old.name) and final.name.startswith("SDJMobileCare_Setup_"):
            old.unlink(missing_ok=True)
    return final


# ---------------------------------------------------------------- installing on the PC
def install_command(installer: Path) -> list[str]:
    return [str(installer), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/UPDATE=1"]


def batch_text(installer: Path) -> str:
    """A tiny script: wait a few seconds so this program can close, then run the installer."""
    return "@echo off\r\nping -n 5 127.0.0.1 >nul\r\n" + subprocess.list2cmdline(install_command(installer)) + "\r\n"


def launch_installer(installer: Path) -> None:
    """Start the update in the background and return; the caller must then close the app so files can be replaced."""
    if not can_self_install():
        raise UpdateError("Automatic install works only in the installed Windows program.")
    bat = updates_dir() / "run_update.bat"
    bat.write_text(batch_text(installer), encoding="utf-8")
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    subprocess.Popen(["cmd", "/c", str(bat)], close_fds=True, creationflags=flags)   # noqa: S603,S607


def prepare_install(latest: dict, progress: Callable[[int, int], bool | None] | None = None) -> Path:
    """Download + verify the installer and save a safety backup. Returns the installer path."""
    path = download(latest["setup"], progress)
    try:
        from .backup import backup_now
        backup_now("before_update")
    except Exception:
        log.exception("backup before update failed")
        raise UpdateError("A safety backup could not be saved, so the update was not started. Please try again.")
    return path


# ---------------------------------------------------------------- the phone app copy kept on the PC
def phone_apk_path(version: str = APP_VERSION) -> Path:
    return updates_dir() / f"SDJMobileCare-Phone-{version}.apk"


def staged_phone_apk() -> dict | None:
    """The phone app that matches this PC's version, if it is saved here: {version, name, sha, size, path}."""
    try:
        p = phone_apk_path()
        meta = settings.get("phone_apk") or {}
        if p.exists() and meta.get("version") == APP_VERSION and meta.get("sha") and meta.get("size") == p.stat().st_size:
            return dict(version=APP_VERSION, name=p.name, sha=meta["sha"], size=meta["size"], path=p,
                        notes=str(meta.get("notes") or ""), date=str(meta.get("date") or ""),
                        recommended=bool(meta.get("recommended")))
    except Exception:
        log.exception("phone app copy check failed")
    return None


def stage_phone_apk() -> bool:
    """Download the phone app for THIS version from GitHub (needs internet on the PC). True when ready."""
    if staged_phone_apk() is not None:
        return True
    try:
        rel = _get_json(f"{API}/tags/v{APP_VERSION}")
        info = _describe(rel)
        if info["apk"] is None or info["version"] != APP_VERSION:
            return False
        path = download(info["apk"])
        settings.set_value("phone_apk", dict(version=APP_VERSION, sha=info["apk"]["sha"], size=path.stat().st_size,
                                             notes=info["notes"][:1500], date=info["date"], recommended=info["recommended"]))
        for old in updates_dir().glob("SDJMobileCare-Phone-*.apk"):
            if old != path:
                old.unlink(missing_ok=True)
        return True
    except Exception as exc:
        log.info("could not save the phone app copy: %s", exc)
        return False


# ---------------------------------------------------------------- "Later" and "What's new after the update"
def snooze(days: int = 1) -> None:
    from datetime import datetime, timedelta
    settings.set_value("update_snooze_until", (datetime.now() + timedelta(days=days)).isoformat(timespec="seconds"))


def snoozed() -> bool:
    from datetime import datetime
    until = settings.get("update_snooze_until") or ""
    try:
        return bool(until) and datetime.now() < datetime.fromisoformat(until)
    except ValueError:
        return False


def remember_for_next_start(latest: dict) -> None:
    """Before installing: keep this version's notes so the new program can show 'What's new' when it opens."""
    settings.set_value("whats_new_next", dict(version=latest["version"], notes=latest.get("notes") or "",
                                              date=latest.get("date") or ""))


def update_history() -> list[dict]:
    return list(settings.get("update_history") or [])


def take_whats_new(fetch: bool = True) -> dict | None:
    """Called once after start. Returns {version, notes, date} the first time a NEW version opens, else None.
    The very first start of a program (nothing remembered) shows nothing."""
    from datetime import date as _date
    last = settings.get("last_seen_version") or ""
    if last == APP_VERSION:
        return None
    settings.set_value("last_seen_version", APP_VERSION)
    nxt = settings.get("whats_new_next") or {}
    entry = None
    if nxt.get("version") == APP_VERSION:
        entry = dict(version=APP_VERSION, notes=str(nxt.get("notes") or ""), date=str(nxt.get("date") or ""))
    elif last and fetch:                       # installed by hand over an older version: read the notes if online
        try:
            info = _describe(_get_json(f"{API}/tags/v{APP_VERSION}"))
            entry = dict(version=APP_VERSION, notes=info["notes"], date=info["date"])
        except Exception as exc:
            log.info("no release notes for the what's new card: %s", exc)
            entry = dict(version=APP_VERSION, notes="", date="")
    elif last:
        entry = dict(version=APP_VERSION, notes="", date="")
    if entry is None:
        return None
    if not entry["date"]:
        entry["date"] = _date.today().isoformat()
    hist = [h for h in update_history() if h.get("version") != APP_VERSION]
    hist.insert(0, entry)
    settings.set_many({"update_history": hist[:20], "whats_new_next": {}})
    return entry
