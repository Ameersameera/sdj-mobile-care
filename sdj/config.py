"""Paths and runtime configuration.

Data layout (Windows defaults):
  Database : %LOCALAPPDATA%\\SDJ Mobile Care\\data\\sdj_mobile_care.db
  Logs     : %LOCALAPPDATA%\\SDJ Mobile Care\\logs
  Documents: Documents\\SDJ Mobile Care\\{Invoices,Reports,Exports,Backups}

Overrides (useful for testing / portable installs):
  * environment variable SDJ_DATA_DIR  -> everything goes under this folder
  * command line --data-dir PATH       -> same as SDJ_DATA_DIR
  * a file named "portable.flag" next to the executable -> data stored beside the exe
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_overrides: dict[str, Path] = {}


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def base_dir() -> Path:
    """Folder that contains the 'assets' directory (works for source and PyInstaller)."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def asset_path(*parts: str) -> Path:
    return base_dir().joinpath("assets", *parts)


def set_data_dir(path: str | Path) -> None:
    _overrides["data"] = Path(path).resolve()


def _default_data_root() -> Path:
    if "data" in _overrides:
        return _overrides["data"]
    env = os.environ.get("SDJ_DATA_DIR")
    if env:
        return Path(env).resolve()
    if is_frozen():
        exe_dir = Path(sys.executable).parent
        if (exe_dir / "portable.flag").exists():
            return exe_dir / "SDJ_Data"
    if sys.platform.startswith("win"):
        root = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(root) / "SDJ Mobile Care"
    return Path.home() / ".local" / "share" / "sdj-mobile-care"


def _uses_custom_root() -> bool:
    return "data" in _overrides or bool(os.environ.get("SDJ_DATA_DIR")) or (
        is_frozen() and (Path(sys.executable).parent / "portable.flag").exists())


def data_root() -> Path:
    p = _default_data_root()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _ensure(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_dir() -> Path:
    return _ensure(data_root() / "data")


def db_path() -> Path:
    return db_dir() / "sdj_mobile_care.db"


def logs_dir() -> Path:
    return _ensure(data_root() / "logs")


def documents_root() -> Path:
    """User-visible folder for PDFs, exports and backups."""
    if _uses_custom_root():
        return _ensure(data_root() / "Documents")
    docs = Path.home() / "Documents"
    if not docs.exists():
        docs = data_root()
    return _ensure(docs / "SDJ Mobile Care")


def invoices_dir() -> Path:
    return _ensure(documents_root() / "Invoices")


def reports_dir() -> Path:
    return _ensure(documents_root() / "Reports")


def exports_dir() -> Path:
    return _ensure(documents_root() / "Exports")


def default_backups_dir() -> Path:
    return _ensure(documents_root() / "Backups")


def logo_store_dir() -> Path:
    return _ensure(data_root() / "branding")
