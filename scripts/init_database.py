"""Database initialisation script.

Creates (or upgrades) the SDJ Mobile Care database with all tables, protective triggers and seed data
(product categories, walk-in customer, default settings). Optionally creates the owner/admin login.

Usage:
    python scripts/init_database.py                       # default location (%LOCALAPPDATA%\\SDJ Mobile Care\\data)
    python scripts/init_database.py --db C:\\path\\shop.db  # specific file
    python scripts/init_database.py --admin owner          # also create an admin user (asks for the password)
    python scripts/init_database.py --check                # integrity check only

Safe to run more than once: existing data is never deleted.
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    from sdj import config
    from sdj.db.init_db import open_database, quick_check
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", help="database file (default: standard location)")
    ap.add_argument("--admin", metavar="USERNAME", help="create an admin login with this username")
    ap.add_argument("--name", default="", help="full name for the admin user")
    ap.add_argument("--check", action="store_true", help="only run the integrity check")
    args = ap.parse_args()
    db = Path(args.db) if args.db else config.db_path()
    if args.check:
        ok, msg = quick_check(db)
        print(f"{db}: {'OK' if ok else 'PROBLEM'} ({msg})")
        return 0 if ok else 1
    open_database(db)
    print(f"Database ready: {db}")
    from sdj.core import auth
    if args.admin:
        pw = getpass.getpass("Password for the admin user (min 6 characters): ")
        if pw != getpass.getpass("Repeat password: "):
            print("Passwords do not match.")
            return 1
        auth.create_user(args.admin, pw, "ADMIN", args.name, check_perm=False)
        print(f"Admin user '{args.admin}' created.")
    elif auth.user_count() == 0:
        print("No users yet - the first start of the app will ask you to create the owner/admin login.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
