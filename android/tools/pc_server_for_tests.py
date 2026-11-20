"""CI helper: runs the real PC sync service on 127.0.0.1 with the fixture shop, for the phone's live HTTP test.

Usage: python android/tools/pc_server_for_tests.py android/build/test-out   (keeps running until killed)
Writes live_code.json (a fresh one-time code and a restore code) for LiveLinkTest.kt. A locked backup is made first,
so the phone downloads it with its first sync and then sends it back like it would to a new PC.
"""
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "android/build/test-out")
OUT.mkdir(parents=True, exist_ok=True)
tmp = Path(tempfile.mkdtemp())
os.environ["SDJ_DATA_DIR"] = str(tmp / "data")
from sdj import config  # noqa: E402
config.db_path().parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(ROOT / "android/app/src/test/resources/fixtures/shop.db", config.db_path())
from sdj.core import auth, phonesync, settings  # noqa: E402
from sdj.core.syncserver import SyncServer  # noqa: E402
from sdj.db.init_db import open_database  # noqa: E402

open_database(config.db_path())
settings.clear_cache()
auth.set_current_user(auth.authenticate("admin", "admin123"))
phonesync.subscribe(lambda event, data: print("event", event, data, flush=True))
from sdj.core import offsite  # noqa: E402
offsite.create_bundle("closing")                      # the phone downloads it with its first sync
info = phonesync.new_link_code(1)
restore = phonesync.new_restore_code()                # the phone sends that backup back (as to a new PC)
srv = SyncServer(port=phonesync.PORT, udp_port=phonesync.DISCOVERY_PORT, host="127.0.0.1")
assert srv.start(), srv.error
(OUT / "live_code.json").write_text(json.dumps(dict(code=info["code"], pc=phonesync.pc_identity(), port=phonesync.PORT,
                                                    restore=restore["code"])))
print("PC sync service for tests on 127.0.0.1:%s, code %s" % (phonesync.PORT, info["display"]), flush=True)
while True:
    time.sleep(60)
