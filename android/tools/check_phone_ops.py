"""CI check: the PC code accepts exactly what the phone app sends (run after the Android unit tests).

Usage: python android/tools/check_phone_ops.py android/build/test-out
"""
import base64
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "android/build/test-out")
FIX = ROOT / "android/app/src/test/resources/fixtures"

tmp = Path(tempfile.mkdtemp())
os.environ["SDJ_DATA_DIR"] = str(tmp / "data")
from sdj import config  # noqa: E402
config.db_path().parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(FIX / "shop.db", config.db_path())
from sdj.core import phonesync, settings  # noqa: E402
from sdj.db.init_db import open_database  # noqa: E402

open_database(config.db_path())
settings.clear_cache()

c = json.loads((FIX / "crypto.json").read_text())
env = json.loads((OUT / "kotlin_envelope.json").read_text())
got = phonesync.unseal(base64.b64decode(c["key"]), env, c["aad"].encode())
assert got["from"] == "phone" and "தமிழ்" in got["text"], got
print("phone → PC envelope: OK")

dev = json.loads((FIX / "device.json").read_text())
key = base64.b64decode(dev["key"])
req_env = json.loads((OUT / "phone_sync_request.json").read_text())
answer = phonesync.unseal(key, phonesync.handle_sync(req_env, "127.0.0.1"), dev["device"].encode())
bad = [r for r in answer["results"] if r["st"] != "DONE"]
for r in answer["results"]:
    print(f"  {r['st']:8} {r.get('res')} {r.get('msg') or ''}")
assert not bad, f"PC did not accept: {bad}"
alerts = phonesync.list_alerts()
print("alerts:", [(a["kind"], a["title"]) for a in alerts])
assert not [a for a in alerts if a["kind"] in ("TOTAL", "FAILED")], "phone totals differ from the PC"
assert len(answer["results"]) == 7
print(f"PC accepted all {len(answer['results'])} phone entries with the same totals: OK")
