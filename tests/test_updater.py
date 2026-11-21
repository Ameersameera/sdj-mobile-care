"""In-app updates (v1.5): GitHub check, safe download, silent installer command, and the phone app copy that the PC
hands to linked phones over the shop Wi-Fi."""
import hashlib
import http.server
import json
import threading
from pathlib import Path

import pytest

from sdj import APP_VERSION
from sdj.core import phonesync, settings, updater
from sdj.core.phonesync import SyncError, seal, unseal
from test_offsite import BackupPhone

SETUP = "SDJMobileCare_Setup_9.9.9.exe"
APK = "SDJMobileCare-Phone-9.9.9.apk"


def release(version="9.9.9", sha_setup="a" * 64, sha_apk="b" * 64, with_setup=True):
    assets = [dict(name=f"SDJMobileCare-Phone-{version}.apk", size=10, digest="sha256:" + sha_apk,
                   browser_download_url=f"https://github.com/x/y/releases/download/v{version}/SDJMobileCare-Phone-{version}.apk")]
    if with_setup:
        assets.append(dict(name=f"SDJMobileCare_Setup_{version}.exe", size=20, digest="sha256:" + sha_setup,
                           browser_download_url=f"https://github.com/x/y/releases/download/v{version}/SDJMobileCare_Setup_{version}.exe"))
    return dict(tag_name=f"v{version}", body="What is new", html_url="https://github.com/x/y/releases/tag/x", assets=assets)


def test_version_compare():
    assert updater.is_newer("1.5.0", "1.4.0")
    assert updater.is_newer("1.10.0", "1.9.9")
    assert not updater.is_newer("1.4.0", "1.4.0")
    assert not updater.is_newer("1.3.9", "1.4.0")
    assert not updater.is_newer("", "1.4.0")


def test_check_new_current_offline(db, monkeypatch):
    monkeypatch.setattr(updater, "_get_json", lambda url, timeout=10: release("9.9.9"))
    res = updater.check()
    assert res["state"] == "new" and res["latest"]["version"] == "9.9.9"
    assert res["latest"]["setup"]["sha"] == "a" * 64 and res["latest"]["apk"]["sha"] == "b" * 64
    assert settings.get("update_last_check")
    monkeypatch.setattr(updater, "_get_json", lambda url, timeout=10: release(APP_VERSION))
    assert updater.check()["state"] == "current"
    monkeypatch.setattr(updater, "_get_json", lambda url, timeout=10: release("9.9.9", with_setup=False))
    assert updater.check()["state"] == "current"          # nothing installable: no popup

    def boom(url, timeout=10):
        raise OSError("no internet")
    monkeypatch.setattr(updater, "_get_json", boom)
    assert updater.check()["state"] == "offline"


def test_drafts_and_prereleases_are_ignored(monkeypatch):
    rel = release("9.9.9")
    rel["prerelease"] = True
    monkeypatch.setattr(updater, "_get_json", lambda url, timeout=10: rel)
    assert updater.fetch_latest() is None


def test_only_github_links_are_used():
    for bad in ("http://github.com/x", "https://evil.example.com/a.exe", "https://github.com.evil.com/a.exe", ""):
        with pytest.raises(updater.UpdateError):
            updater._check_url(bad)
    updater._check_url("https://github.com/x/y/releases/download/v1/a.exe")
    updater._check_url("https://objects.githubusercontent.com/abc")


def test_auto_check_timing(db):
    from datetime import datetime, timedelta
    assert updater.should_check_now()
    settings.set_value("update_last_check", datetime.now().isoformat())
    assert not updater.should_check_now()
    settings.set_value("update_last_check", (datetime.now() - timedelta(hours=13)).isoformat())
    assert updater.should_check_now()
    settings.set_value("update_auto_check", False)
    assert not updater.should_check_now()


class _Files(http.server.BaseHTTPRequestHandler):
    body = b"x" * 300_000

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *a):
        pass


@pytest.fixture
def web(monkeypatch):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Files)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(updater, "_check_url", lambda url: None)
    yield f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()


def test_download_verifies_checksum(db, web, tmp_path):
    good = hashlib.sha256(_Files.body).hexdigest()
    seen = []
    p = updater.download(dict(name=SETUP, url=web + SETUP, size=len(_Files.body), sha=good),
                         lambda d, t: seen.append((d, t)), tmp_path / "dl")
    assert p.read_bytes() == _Files.body and seen and seen[-1][0] == len(_Files.body)
    assert not list((tmp_path / "dl").glob("*.part"))
    # already there and correct: no second download needed
    assert updater.download(dict(name=SETUP, url="http://127.0.0.1:1/never", sha=good), None, tmp_path / "dl") == p


def test_download_rejects_wrong_checksum_and_missing_checksum(db, web, tmp_path):
    with pytest.raises(updater.UpdateError, match="checksum"):
        updater.download(dict(name=SETUP, url=web + SETUP, sha="0" * 64), None, tmp_path / "dl")
    assert not list((tmp_path / "dl").glob("*"))
    with pytest.raises(updater.UpdateError, match="no checksum"):
        updater.download(dict(name=SETUP, url=web + SETUP, sha=""), None, tmp_path / "dl")
    with pytest.raises(updater.UpdateError):
        updater.download(dict(name="..\\evil.exe", url=web + "x", sha="a" * 64), None, tmp_path / "dl")


def test_download_can_be_cancelled(db, web, tmp_path):
    good = hashlib.sha256(_Files.body).hexdigest()
    with pytest.raises(updater.UpdateError, match="Cancelled"):
        updater.download(dict(name=SETUP, url=web + SETUP, sha=good), lambda d, t: False, tmp_path / "dl")
    assert not list((tmp_path / "dl").glob("*"))


def test_installer_command_and_batch(tmp_path):
    cmd = updater.install_command(tmp_path / SETUP)
    assert cmd[0].endswith(SETUP) and "/SILENT" in cmd and "/UPDATE=1" in cmd and "/CLOSEAPPLICATIONS" in cmd
    text = updater.batch_text(tmp_path / SETUP)
    assert "ping -n" in text and SETUP in text and "/SILENT" in text


def test_source_checkout_cannot_self_install(tmp_path):
    assert not updater.can_self_install()
    with pytest.raises(updater.UpdateError):
        updater.launch_installer(tmp_path / SETUP)


def test_backup_is_saved_before_install(db, monkeypatch, tmp_path):
    good = hashlib.sha256(b"abc").hexdigest()
    made = []
    monkeypatch.setattr(updater, "download", lambda asset, progress=None, dest_dir=None: tmp_path / SETUP)
    import sdj.core.backup as backup
    monkeypatch.setattr(backup, "backup_now", lambda label="manual", **kw: made.append(label) or tmp_path / "b.db")
    path = updater.prepare_install(dict(setup=dict(name=SETUP, url="x", sha=good)))
    assert path.name == SETUP and made == ["before_update"]

    def fail(label="manual", **kw):
        raise OSError("disk full")
    monkeypatch.setattr(backup, "backup_now", fail)
    with pytest.raises(updater.UpdateError, match="safety backup"):
        updater.prepare_install(dict(setup=dict(name=SETUP, url="x", sha=good)))


# ---------------------------------------------------------------- phones
def _stage(db_tmp, data=b"PK-fake-apk" * 1000):
    p = updater.phone_apk_path()
    p.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    settings.set_value("phone_apk", dict(version=APP_VERSION, sha=sha, size=len(data)))
    return p, sha, data


def _apk_env(phone, aad=b"|apk"):
    phone.seq += 1
    env = seal(phone.key, {"seq": phone.seq, "app": "t"}, phone.device.encode() + aad)
    env["d"] = phone.device
    return env


def test_phone_is_offered_the_matching_app_and_can_download_it(db):
    phone = BackupPhone()
    phone.pair(phonesync.new_link_code(1)["code"], name="Owner phone")
    assert "update" not in phone.sync(full=True)                    # nothing saved on the PC yet
    path, sha, data = _stage(db)
    ans = phone.sync()
    assert ans["update"]["version"] == APP_VERSION and ans["update"]["sha"] == sha and ans["update"]["size"] == len(data)
    got = phonesync.handle_apk(_apk_env(phone), "127.0.0.1")
    assert Path(got["path"]).read_bytes() == data and got["sha"] == sha


def test_phone_already_on_this_version_is_not_offered_the_app(db):
    phone = BackupPhone()
    phone.pair(phonesync.new_link_code(1)["code"], name="Owner phone")
    _stage(db)
    req = {"seq": phone.seq + 1, "ops": [], "token": phone.token, "hashes": phone.hashes, "full": False,
           "app": APP_VERSION, "acks": []}
    phone.seq += 1
    env = seal(phone.key, req, phone.device.encode())
    env["d"] = phone.device
    ans = phone.read(phonesync.handle_sync(env, "127.0.0.1"))
    assert "update" not in ans


def test_apk_request_needs_a_linked_phone_and_a_saved_copy(db):
    phone = BackupPhone()
    phone.pair(phonesync.new_link_code(1)["code"], name="Owner phone")
    with pytest.raises(SyncError) as e:
        phonesync.handle_apk(_apk_env(phone), "127.0.0.1")           # nothing saved
    assert e.value.code == "no_update"
    _stage(db)
    with pytest.raises(Exception):
        phonesync.handle_apk(_apk_env(phone, aad=b"|backup"), "127.0.0.1")   # wrong purpose
    bad = _apk_env(phone)
    bad["d"] = "not-a-device"
    with pytest.raises(Exception):
        phonesync.handle_apk(bad, "127.0.0.1")                       # unknown phone


def test_phone_app_copy_for_another_version_is_not_used(db):
    p, sha, data = _stage(db)
    settings.set_value("phone_apk", dict(version="0.0.1", sha=sha, size=len(data)))
    assert updater.staged_phone_apk() is None
    settings.set_value("phone_apk", dict(version=APP_VERSION, sha=sha, size=len(data) + 1))
    assert updater.staged_phone_apk() is None                         # size changed: not trusted
    p.unlink()
    settings.set_value("phone_apk", dict(version=APP_VERSION, sha=sha, size=len(data)))
    assert updater.staged_phone_apk() is None


def test_stage_phone_apk_downloads_for_this_version(db, web, monkeypatch):
    sha = hashlib.sha256(_Files.body).hexdigest()
    rel = release(APP_VERSION, sha_apk=sha)
    rel["assets"][0]["browser_download_url"] = web + f"SDJMobileCare-Phone-{APP_VERSION}.apk"
    monkeypatch.setattr(updater, "_get_json", lambda url, timeout=10: rel)
    assert updater.stage_phone_apk()
    s = updater.staged_phone_apk()
    assert s and s["sha"] == sha and s["size"] == len(_Files.body)
    assert updater.stage_phone_apk()                                  # already there: nothing more to fetch


def test_stage_phone_apk_without_internet_is_quiet(db, monkeypatch):
    def boom(url, timeout=10):
        raise OSError("no internet")
    monkeypatch.setattr(updater, "_get_json", boom)
    assert updater.stage_phone_apk() is False
