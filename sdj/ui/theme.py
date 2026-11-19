"""Light & dark themes derived from the SDJ logo (charcoal, cream, gold)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    name: str
    bg: str
    card: str
    card_alt: str
    border: str
    input_bg: str
    text: str
    text2: str
    muted: str
    accent: str          # gold – fills, highlights
    accent_text: str     # gold dark enough to be read as text
    on_accent: str
    sidebar: str
    sidebar_text: str
    sidebar_muted: str
    sidebar_hover: str
    sidebar_active: str
    header_bg: str
    table_head: str
    selection: str
    good: str
    warn: str
    bad: str
    info: str
    # validated chart steps (see dataviz validator: categorical, adjacent CVD ΔE ≥ 25)
    series1: str
    series2: str
    grid: str


LIGHT = Palette(
    name="light", bg="#F6F3EA", card="#FFFFFF", card_alt="#FBF8F0", border="#E6DFCC", input_bg="#FFFFFF",
    text="#1D1F15", text2="#4F4E44", muted="#86847A", accent="#CFAA51", accent_text="#8C6D31", on_accent="#1D1F15",
    sidebar="#1D1F15", sidebar_text="#E9E4D3", sidebar_muted="#8F8C7B", sidebar_hover="#2A2C21",
    sidebar_active="#3A3423", header_bg="#FFFFFF", table_head="#F4EEDD", selection="#F1E3BC",
    good="#1E7B34", warn="#B26B00", bad="#B42318", info="#2A6FB8",
    series1="#A87A1F", series2="#2A78D6", grid="#ECE7DA",
)

DARK = Palette(
    name="dark", bg="#121309", card="#1C1D16", card_alt="#212219", border="#33342A", input_bg="#17180F",
    text="#EFEBDD", text2="#C4C0AE", muted="#8F8C7C", accent="#D4AF5A", accent_text="#E0C27A", on_accent="#15160F",
    sidebar="#0C0D07", sidebar_text="#E4DFCD", sidebar_muted="#7F7C6C", sidebar_hover="#1D1E15",
    sidebar_active="#2E2A1C", header_bg="#17180F", table_head="#26271E", selection="#4A4128",
    good="#4CBF65", warn="#E8A33A", bad="#F0705F", info="#6AA8F0",
    series1="#B08834", series2="#3987E5", grid="#2B2C23",
)

_current = LIGHT


def current() -> Palette:
    return _current


def set_theme(name: str) -> Palette:
    global _current
    _current = DARK if name == "dark" else LIGHT
    return _current


def _icon_png(name: str, color: str, size: int = 14, stroke: float = 2.4) -> str:
    """Render a Lucide icon to PNG for use inside the stylesheet (QSS needs an image file)."""
    from .. import config
    from .icons import pixmap
    out = config.data_root() / "ui"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{name}-{color.strip('#')}-{size}.png"
    if not path.exists():
        pm = pixmap(name, color, size, stroke)
        pm.setDevicePixelRatio(1)
        pm.save(str(path))
    return path.as_posix()


def _arrow_png(color: str) -> str:
    return _icon_png("chevron-down", color)


def stylesheet(p: Palette | None = None) -> str:
    p = p or _current
    try:
        arrow = _arrow_png(p.text2)
        tick = _icon_png("check", p.on_accent, 12, 3.2)
    except Exception:
        arrow = tick = ""
    return f"""
* {{ font-family: "Segoe UI", "Noto Sans", "DejaVu Sans", sans-serif; font-size: 10pt; color: {p.text}; }}
QMainWindow, QDialog, #PageRoot, QStackedWidget > QWidget {{ background: {p.bg}; }}
QWidget#Transparent {{ background: transparent; }}
QToolTip {{ background: {p.text}; color: {p.card}; border: none; padding: 6px 8px; border-radius: 6px; }}

/* ---------- sidebar ---------- */
#Sidebar {{ background: {p.sidebar}; border: none; }}
#Sidebar QLabel {{ color: {p.sidebar_text}; background: transparent; }}
#BrandName {{ font-size: 13pt; font-weight: 700; color: {p.accent}; }}
#BrandSub {{ font-size: 8pt; color: {p.sidebar_muted}; letter-spacing: 1px; }}
#SideList {{ background: transparent; border: none; outline: 0; padding: 4px 8px; }}
#SideList::item {{ color: {p.sidebar_text}; padding: 7px 10px; margin: 1px 0; border-radius: 8px; }}
#SideList::item:hover {{ background: {p.sidebar_hover}; }}
#SideList::item:selected {{ background: {p.sidebar_active}; color: {p.accent}; font-weight: 600; }}
#SideList::item:disabled {{ color: {p.sidebar_muted}; font-size: 7.5pt; font-weight: 700; padding: 10px 10px 3px 10px; background: transparent; }}
#SideUser {{ color: {p.sidebar_text}; font-weight: 600; }}
#SideRole {{ color: {p.sidebar_muted}; font-size: 8pt; }}
QPushButton#SideButton {{ background: {p.sidebar_hover}; color: {p.sidebar_text}; border: 1px solid #3A3C2F; border-radius: 8px; padding: 6px 10px; }}
QPushButton#SideButton:hover {{ border-color: {p.accent}; color: {p.accent}; }}

/* ---------- header ---------- */
#Header {{ background: {p.header_bg}; border: none; border-bottom: 1px solid {p.border}; }}
#PageTitle {{ font-size: 16pt; font-weight: 700; }}
#PageSub {{ color: {p.muted}; font-size: 9pt; }}
#DayPill {{ background: {p.card_alt}; border: 1px solid {p.border}; border-radius: 13px; padding: 4px 12px; color: {p.text2}; font-weight: 600; }}
#DayPill[state="closed"] {{ border-color: {p.warn}; color: {p.warn}; }}
#AlertPill {{ background: transparent; border: 1px solid {p.bad}; border-radius: 13px; padding: 4px 12px; color: {p.bad}; font-weight: 600; }}
QToolButton#HeaderBtn {{ background: transparent; border: 1px solid {p.border}; border-radius: 8px; padding: 5px; }}
QToolButton#HeaderBtn:hover {{ border-color: {p.accent}; }}

/* ---------- cards & text ---------- */
#Card, #KpiCard {{ background: {p.card}; border: 1px solid {p.border}; border-radius: 12px; }}
#Card QLabel, #KpiCard QLabel {{ background: transparent; }}
#SectionTitle {{ font-size: 11.5pt; font-weight: 700; }}
#SectionHint, #Muted {{ color: {p.muted}; }}
#KpiTitle {{ color: {p.text2}; font-size: 9pt; }}
#KpiValue {{ font-size: 17pt; font-weight: 700; }}
#KpiSub {{ color: {p.muted}; font-size: 8.5pt; }}
#IconBubble {{ background: {p.table_head}; border-radius: 17px; }}
#BigTotal {{ font-size: 24pt; font-weight: 800; color: {p.text}; }}
#BigTotalLabel {{ color: {p.text2}; font-size: 9pt; font-weight: 600; letter-spacing: 1px; }}
#GoodText {{ color: {p.good}; font-weight: 600; }}
#BadText {{ color: {p.bad}; font-weight: 600; }}
#WarnText {{ color: {p.warn}; font-weight: 600; }}
#Banner {{ background: {p.card_alt}; border: 1px solid {p.accent}; border-radius: 10px; padding: 8px 12px; color: {p.text2}; }}
#BannerWarn {{ background: {p.card_alt}; border: 1px solid {p.warn}; border-radius: 10px; padding: 8px 12px; color: {p.warn}; }}
#Toast {{ background: {p.text}; color: {p.card}; border-radius: 10px; padding: 10px 16px; font-weight: 600; }}
#Toast[kind="error"] {{ background: {p.bad}; color: white; }}

/* ---------- buttons ---------- */
QPushButton {{ background: {p.card}; border: 1px solid {p.border}; border-radius: 8px; padding: 6px 14px; min-height: 18px; }}
QPushButton:hover {{ border-color: {p.accent}; background: {p.card_alt}; }}
QPushButton:pressed {{ background: {p.table_head}; }}
QPushButton:disabled {{ color: {p.muted}; background: {p.card_alt}; border-color: {p.border}; }}
QPushButton[variant="primary"] {{ background: {p.accent}; color: {p.on_accent}; border: 1px solid {p.accent}; font-weight: 700; }}
QPushButton[variant="primary"]:hover {{ background: {p.accent_text}; color: #FFFFFF; border-color: {p.accent_text}; }}
QPushButton[variant="primary"]:disabled {{ background: {p.border}; color: {p.muted}; border-color: {p.border}; }}
QPushButton[variant="danger"] {{ background: transparent; color: {p.bad}; border: 1px solid {p.bad}; font-weight: 600; }}
QPushButton[variant="danger"]:hover {{ background: {p.bad}; color: white; }}
QPushButton[variant="ghost"] {{ background: transparent; border: 1px solid transparent; color: {p.accent_text}; font-weight: 600; }}
QPushButton[variant="ghost"]:hover {{ border-color: {p.border}; }}
QPushButton[variant="quick"] {{ background: {p.card}; border: 1px solid {p.border}; border-radius: 10px; padding: 9px 14px; font-weight: 600; text-align: left; }}
QPushButton[variant="quick"]:hover {{ border-color: {p.accent}; color: {p.accent_text}; }}
QPushButton[variant="seg"] {{ padding: 8px 16px; font-weight: 600; }}
QPushButton[variant="seg"]:checked {{ background: {p.accent}; color: {p.on_accent}; border-color: {p.accent}; }}
QPushButton[variant="chip"] {{ border-radius: 14px; padding: 4px 12px; font-size: 9pt; }}
QPushButton[variant="chip"]:checked {{ background: {p.text}; color: {p.card}; border-color: {p.text}; }}

/* ---------- inputs ---------- */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QTextEdit, QPlainTextEdit {{
    background: {p.input_bg}; border: 1px solid {p.border}; border-radius: 7px; padding: 5px 8px;
    selection-background-color: {p.selection}; selection-color: {p.text}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{ border: 1px solid {p.accent}; }}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{ color: {p.muted}; background: {p.card_alt}; }}
QLineEdit#SearchBox {{ padding-left: 10px; border-radius: 16px; }}
QLineEdit#BigInput {{ font-size: 12pt; padding: 8px 10px; }}
QComboBox::drop-down, QDateEdit::drop-down {{ border: none; width: 24px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow, QDateEdit::down-arrow {{ image: url("{arrow}"); width: 12px; height: 12px; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width: 0px; border: none; image: none; }}
QComboBox QAbstractItemView {{ background: {p.card}; border: 1px solid {p.border}; selection-background-color: {p.selection}; selection-color: {p.text}; outline: 0; }}
QCheckBox, QRadioButton {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 14px; height: 14px; }}
QCheckBox::indicator {{ border: 2px solid {p.muted}; border-radius: 4px; background: {p.input_bg}; }}
QCheckBox::indicator:checked {{ background: {p.accent}; border-color: {p.accent}; image: url("{tick}"); }}
QRadioButton::indicator {{ border: 2px solid {p.muted}; border-radius: 9px; background: {p.input_bg}; }}
QRadioButton::indicator:checked {{ border: 5px solid {p.accent}; background: {p.input_bg}; width: 8px; height: 8px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {p.accent}; }}
QLabel {{ background: transparent; }}
QGroupBox {{ border: 1px solid {p.border}; border-radius: 10px; margin-top: 14px; padding: 12px 10px 10px 10px; background: {p.card}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {p.accent_text}; font-weight: 700; }}

/* ---------- tables ---------- */
QTableView, QTableWidget, QListWidget#PlainList {{ background: {p.card}; alternate-background-color: {p.card_alt}; border: 1px solid {p.border};
    border-radius: 10px; gridline-color: transparent; selection-background-color: {p.selection}; selection-color: {p.text}; outline: 0; }}
QTableView::item, QTableWidget::item {{ padding: 4px 6px; border: none; }}
QHeaderView::section {{ background: {p.table_head}; color: {p.text2}; border: none; border-bottom: 1px solid {p.border};
    padding: 6px 8px; font-weight: 700; font-size: 9pt; }}
QTableCornerButton::section {{ background: {p.table_head}; border: none; }}

/* ---------- tabs ---------- */
QTabWidget::pane {{ border: none; top: -1px; }}
QTabBar::tab {{ background: transparent; padding: 8px 16px; margin-right: 4px; color: {p.text2}; border-bottom: 2px solid transparent; font-weight: 600; }}
QTabBar::tab:selected {{ color: {p.text}; border-bottom: 2px solid {p.accent}; }}
QTabBar::tab:hover {{ color: {p.accent_text}; }}

/* ---------- misc ---------- */
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p.border}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {p.muted}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {p.border}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QMenu {{ background: {p.card}; border: 1px solid {p.border}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {p.selection}; }}
QStatusBar {{ background: {p.header_bg}; color: {p.muted}; border-top: 1px solid {p.border}; }}
QStatusBar QLabel {{ color: {p.muted}; font-size: 8.5pt; }}
QProgressBar {{ border: 1px solid {p.border}; border-radius: 6px; text-align: center; background: {p.card_alt}; }}
QProgressBar::chunk {{ background: {p.accent}; border-radius: 5px; }}
QSplitter::handle {{ background: transparent; }}
"""
