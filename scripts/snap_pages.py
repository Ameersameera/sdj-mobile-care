"""Developer tool: render pages offscreen to PNG (used for visual checks / README screenshots).

Usage: python scripts/snap_pages.py DATA_DIR OUT_DIR page1 [page2 ...] [--theme dark]
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    theme = "dark" if "--theme" in sys.argv and sys.argv[sys.argv.index("--theme") + 1] == "dark" else "light"
    data_dir, out_dir, pages = args[0], Path(args[1]), [a for a in args[2:] if a not in ("dark", "light")]
    out_dir.mkdir(parents=True, exist_ok=True)
    from sdj import config
    config.set_data_dir(data_dir)
    from PySide6.QtWidgets import QApplication, QMessageBox, QDialog
    from PySide6.QtGui import QFont
    app = QApplication(sys.argv[:1])
    # never block on modal dialogs while snapping – print them instead
    for name in ("critical", "warning", "information", "question"):
        setattr(QMessageBox, name, staticmethod(lambda *a, _n=name, **k: print(f"[{_n}]", *[x for x in a if isinstance(x, str)]) or QMessageBox.StandardButton.No))
    QMessageBox.exec = lambda self: print("[msgbox]", self.text()) or 0
    QDialog.exec = lambda self: print("[dialog]", self.windowTitle()) or 0
    app.setFont(QFont("Noto Sans", 10))
    from sdj.db.init_db import open_database
    open_database(config.db_path())
    from sdj.core import auth, settings
    from sdj.core.auth import ALL_PERMISSIONS, CurrentUser
    auth.set_current_user(CurrentUser(1, "admin", "Shop Owner", "ADMIN", set(ALL_PERMISSIONS)))
    from sdj.ui.theme import set_theme, stylesheet
    set_theme(theme)
    app.setStyleSheet(stylesheet())
    from sdj.ui.main_window import MainWindow
    win = MainWindow()
    win.resize(1440, 900)
    win.show()
    for key in pages:
        payload = None
        if ":" in key:
            key, payload = key.split(":", 1)
            payload = {"tab": payload}
        win.navigate(key, payload)
        for _ in range(30):
            app.processEvents()
        page = win.pages.get(key)
        if page is not None and hasattr(page, "refresh"):
            page.refresh()
        for _ in range(30):
            app.processEvents()
        name = key + (f"_{payload['tab']}" if payload else "") + ("_dark" if theme == "dark" else "")
        win.grab().save(str(out_dir / f"{name}.png"))
        print("saved", name)


if __name__ == "__main__":
    main()
