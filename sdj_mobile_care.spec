# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build file for SDJ Mobile Care (one-folder build = fastest start-up).
# Build:  pyinstaller --noconfirm sdj_mobile_care.spec
import glob
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(SPECPATH)  # noqa: F821 – provided by PyInstaller
sys.path.insert(0, ROOT)
# every module of the app, found from the file system (screens are imported lazily by name)
app_modules = []
for path in glob.glob(os.path.join(ROOT, "sdj", "**", "*.py"), recursive=True):
    rel = os.path.relpath(path, ROOT)[:-3].replace(os.sep, ".")
    app_modules.append(rel[:-9] if rel.endswith(".__init__") else rel)

hidden = (app_modules + collect_submodules("sdj") + collect_submodules("reportlab.graphics.barcode")
          + collect_submodules("reportlab.pdfbase")
          + ["sqlalchemy.dialects.sqlite", "sqlalchemy.sql.default_comparator",
             "matplotlib.backends.backend_qtagg", "reportlab.graphics.barcode.qr",
             "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtSvg", "PySide6.QtPrintSupport"])

excludes = ["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
            "PySide6.QtQuick", "PySide6.QtQml", "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtMultimedia",
            "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtBluetooth", "PySide6.QtNfc",
            "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtSensors", "PySide6.QtSerialPort",
            "PySide6.QtWebSockets", "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtSql", "PySide6.QtTest",
            "pytest", "IPython", "notebook", "scipy", "pandas", "lxml", "gi", "sympy", "PyQt5", "PyQt6",
            "matplotlib.tests", "numpy.tests"]

a = Analysis(
    ["main.py"],
    pathex=[ROOT],
    binaries=[],
    datas=[("assets", "assets"), ("sample_data/sdj_sample.db", "sample_data")],
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SDJMobileCare",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon="assets/app.ico",
    version="installer/version_info.txt",
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="SDJMobileCare")
