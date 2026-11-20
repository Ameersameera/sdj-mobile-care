"""Application start-up: logging, crash recovery, single instance, login, main window, backups."""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QLockFile, Qt, QTimer
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from .. import APP_NAME, APP_SHORT_TITLE, APP_VERSION, config

log = logging.getLogger("sdj")


def _setup_logging() -> Path:
    path = config.logs_dir() / "sdj_mobile_care.log"
    handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    return path


def _install_excepthook(app: QApplication) -> None:
    def hook(exc_type, exc, tb):
        logging.getLogger("sdj.crash").error("Unhandled error:\n%s", "".join(traceback.format_exception(exc_type, exc, tb)))
        try:
            QMessageBox.critical(None, "Unexpected error",
                                 f"An unexpected error occurred:\n\n{exc}\n\nYour saved data is safe. Details were "
                                 f"written to the log file in:\n{config.logs_dir()}")
        except Exception:
            pass
    sys.excepthook = hook


def _prepare_demo(data_dir: Path) -> None:
    sample = config.base_dir() / "sample_data" / "sdj_sample.db"
    target = data_dir / "data" / "sdj_mobile_care.db"
    if not target.exists() and sample.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sample, target)


def _recover_database(db: Path) -> bool:
    """Called when the database fails its integrity check. Returns True when usable again."""
    from ..core.backup import latest_good_backup, restore_backup
    from ..db.init_db import quick_check
    ok, msg = quick_check(db)
    if ok:
        return True
    log.error("Database integrity check failed: %s", msg)
    latest = latest_good_backup()
    if latest is None:
        QMessageBox.critical(None, "Database problem",
                             f"The shop database could not be read ({msg}) and no backup was found.\n\n"
                             f"Database: {db}\nPlease contact support before entering new data.")
        return False
    ans = QMessageBox.question(None, "Restore from backup?",
                               f"The shop database appears damaged ({msg}).\n\nRestore the most recent good backup?\n"
                               f"{latest.name}\n\nThe damaged file will be kept as a copy.",
                               QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
    if ans != QMessageBox.Yes:
        return False
    try:
        restore_backup(latest)
    except Exception as exc:
        QMessageBox.critical(None, "Restore failed", str(exc))
        return False
    QMessageBox.information(None, "Restored", "The backup was restored successfully.")
    return True


def run(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    parser = argparse.ArgumentParser(prog="SDJMobileCare", add_help=True)
    parser.add_argument("--data-dir", help="Folder for the database, logs and documents")
    parser.add_argument("--demo", action="store_true", help="Open the sample/demo database (separate from real data)")
    parser.add_argument("--self-test", action="store_true", help="Check this installation (uses a temporary folder)")
    args, qt_args = parser.parse_known_args(argv[1:])
    if args.self_test:
        from ..selftest import run_self_test
        return run_self_test()
    if args.data_dir:
        config.set_data_dir(args.data_dir)
    elif args.demo:
        config.set_data_dir(config._default_data_root() / "demo")
    if args.demo:
        _prepare_demo(config.data_root())
    log_path = _setup_logging()
    log.info("Starting %s v%s (data: %s)", APP_NAME, APP_VERSION, config.data_root())

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication([argv[0]] + qt_args)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("SDJ Mobile Care")
    app.setApplicationDisplayName(APP_SHORT_TITLE)
    icon_path = config.asset_path("app.ico")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    f = QFont("Segoe UI" if sys.platform.startswith("win") else "Noto Sans", 10)
    app.setFont(f)
    _install_excepthook(app)

    lock = QLockFile(str(config.data_root() / "sdj.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(200):
        QMessageBox.warning(None, APP_NAME, "SDJ Mobile Care is already open on this computer.\n"
                                            "Use the window that is already running.")
        return 1

    from ..db.init_db import open_database
    db = config.db_path()
    if db.exists() and not _recover_database(db):
        return 2
    try:
        open_database(db)
    except Exception as exc:
        log.exception("database open failed")
        if not _recover_database(db):
            QMessageBox.critical(None, "Database error", f"Could not open the database:\n{exc}\n\nLog: {log_path}")
            return 2
        open_database(db)

    from .phone_link import start_bridge
    start_bridge()                                   # phones can sync even while the login screen is open

    from ..core import settings
    from ..core.auth import user_count
    from ..core.backup import auto_backup_if_due, backup_now
    from .theme import set_theme, stylesheet
    set_theme(settings.get("theme") or "light")
    app.setStyleSheet(stylesheet())

    from .login import LoginDialog, SetupWizard

    def sign_in() -> bool:
        while user_count() == 0:                     # new PC: restore the shop, or start fresh
            wiz = SetupWizard()
            if wiz.exec() != QDialog.Accepted:
                return False
            if wiz.user is not None:
                return True
            set_theme(settings.get("theme") or "light")   # restored: the shop's own settings are back
            app.setStyleSheet(stylesheet())
        return LoginDialog().exec() == QDialog.Accepted

    if not sign_in():
        return 0
    QTimer.singleShot(1500, lambda: auto_backup_if_due(20))

    from .main_window import MainWindow
    state = {"win": None}

    def open_main():
        set_theme(settings.get("theme") or "light")
        app.setStyleSheet(stylesheet())
        win = MainWindow()
        win.logout_requested.connect(on_logout)
        state["win"] = win
        win.showMaximized()

    def on_logout():
        win = state["win"]
        if win is not None:
            win.hide()
            win.deleteLater()
        state["win"] = None
        if sign_in():
            open_main()
        else:
            app.quit()

    open_main()

    def on_quit():
        try:
            from ..core.syncserver import service
            service().stop()
        except Exception:
            log.exception("stopping phone sync failed")
        try:
            from datetime import datetime, timedelta
            last = settings.get("last_backup_at") or ""
            if settings.get("auto_backup") and (not last or datetime.now() - datetime.fromisoformat(last) > timedelta(hours=4)):
                backup_now("exit")
        except Exception:
            log.exception("exit backup failed")
        lock.unlock()

    app.aboutToQuit.connect(on_quit)
    return app.exec()
