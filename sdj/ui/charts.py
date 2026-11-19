"""Matplotlib charts embedded in Qt – thin rounded bars, hairline grid, one axis, hover tooltips.

Colours are the validated categorical steps from the theme (gold, blue) – checked with the
dataviz palette validator for both light and dark surfaces.
"""
from __future__ import annotations

from decimal import Decimal

import matplotlib

matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.patches import FancyBboxPatch, Rectangle  # noqa: E402
from matplotlib.ticker import FuncFormatter, MaxNLocator  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget  # noqa: E402

from ..output.fmt import inr  # noqa: E402
from .theme import current  # noqa: E402

matplotlib.rcParams["font.family"] = "DejaVu Sans"
matplotlib.rcParams["font.size"] = 8.5


def compact_inr(v: float, _pos=None) -> str:
    a = abs(v)
    sign = "-" if v < 0 else ""
    if a >= 1e7:
        return f"{sign}₹{a / 1e7:.1f}Cr".replace(".0Cr", "Cr")
    if a >= 1e5:
        return f"{sign}₹{a / 1e5:.1f}L".replace(".0L", "L")
    if a >= 1e3:
        return f"{sign}₹{a / 1e3:.0f}K"
    return f"{sign}₹{a:.0f}"


class ChartWidget(QWidget):
    def __init__(self, parent=None, height: int = 260):
        super().__init__(parent)
        self.fig = Figure(figsize=(5, 2.6), dpi=100)
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.canvas)
        self.setMinimumHeight(height)
        self._spec = None
        self._hits = []
        self._tip = None
        self._cross = None
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._render)
        self.canvas.mpl_connect("motion_notify_event", self._on_move)
        self.canvas.mpl_connect("axes_leave_event", lambda e: self._hide_tip())

    # ---------------------------------------------------------------- public API
    def bars(self, labels, values, title: str = "", series_name: str = "Value", horizontal: bool = False):
        self._spec = dict(kind="hbar" if horizontal else "bar", labels=list(labels),
                          series=[(series_name, [float(v) for v in values])], title=title)
        self._render()

    def grouped(self, labels, series: list[tuple[str, list]], title: str = ""):
        self._spec = dict(kind="grouped", labels=list(labels), series=[(n, [float(v) for v in vals]) for n, vals in series],
                          title=title)
        self._render()

    def stacked(self, labels, series: list[tuple[str, list]], title: str = ""):
        self._spec = dict(kind="stacked", labels=list(labels), series=[(n, [float(v) for v in vals]) for n, vals in series],
                          title=title)
        self._render()

    def lines(self, labels, series: list[tuple[str, list]], title: str = ""):
        self._spec = dict(kind="line", labels=list(labels), series=[(n, [float(v) for v in vals]) for n, vals in series],
                          title=title)
        self._render()

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        if self._spec:
            self._resize_timer.start(120)

    # ---------------------------------------------------------------- rendering
    def _colors(self):
        p = current()
        return [p.series1, p.series2]

    def _render(self):
        spec = self._spec
        if not spec:
            return
        p = current()
        self.fig.clear()
        self.fig.set_facecolor(p.card)
        ax = self.fig.add_subplot(111)
        self.ax = ax
        ax.set_facecolor(p.card)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(p.grid)
        ax.tick_params(colors=p.text2, length=0, labelsize=8)
        self._hits = []
        self._tip = None
        self._cross = None
        labels, series = spec["labels"], spec["series"]
        empty = not labels or all(abs(v) < 1e-9 for _, vals in series for v in vals)
        if spec["kind"] == "hbar":
            self._render_hbar(ax, labels, series[0])
        elif spec["kind"] == "line":
            self._render_lines(ax, labels, series)
        else:
            self._render_bars(ax, labels, series, stacked=spec["kind"] == "stacked")
        if spec.get("title"):
            ax.set_title(spec["title"], loc="left", color=p.text, fontsize=9.5, fontweight="bold", pad=10)
        if len(series) > 1:
            leg = ax.legend(loc="upper left", bbox_to_anchor=(0, 1.02), ncol=len(series), frameon=False, fontsize=8,
                            handlelength=1.0, handleheight=0.8, borderaxespad=0)
            for t in leg.get_texts():
                t.set_color(p.text2)
        if empty:
            ax.text(0.5, 0.5, "No data yet for this period", transform=ax.transAxes, ha="center", va="center",
                    color=p.muted, fontsize=9)
        self.fig.tight_layout(pad=0.8)
        self.canvas.draw_idle()

    def _px(self, ax):
        """Data units per pixel for x and y (after limits are set)."""
        bbox = ax.get_window_extent()
        x0, x1 = ax.get_xlim()
        y0, y1 = ax.get_ylim()
        return (x1 - x0) / max(bbox.width, 1), (y1 - y0) / max(bbox.height, 1)

    def _rounded_bar(self, ax, x_left, width, base, top, color, radius_px=4.0):
        """Column with a 4px rounded data-end and a square base (grows from one baseline)."""
        xpp, ypp = self._px(ax)
        height = top - base
        if abs(height) < 1e-12:
            return
        r_x = min(radius_px * xpp, width / 2, (abs(height) * xpp / ypp) / 2)
        r_y = r_x * (ypp / xpp)
        y0, h = (base, height) if height > 0 else (top, -height)
        patch = FancyBboxPatch((x_left, y0), width, h, boxstyle=f"round,pad=0,rounding_size={r_x}",
                               mutation_aspect=ypp / xpp, linewidth=0, facecolor=color)
        ax.add_patch(patch)
        sq_h = min(abs(height), r_y)
        sq_y = base if height > 0 else base - sq_h
        ax.add_patch(Rectangle((x_left, sq_y), width, sq_h, linewidth=0, facecolor=color))

    def _render_bars(self, ax, labels, series, stacked: bool):
        p = current()
        n = len(labels)
        cols = self._colors()
        ax.set_xlim(-0.6, n - 0.4)
        if stacked:
            tops = [sum(max(v, 0) for v in vals) for vals in zip(*[s[1] for s in series])] or [0]
            lo = 0
            hi = max(tops) if tops else 0
        else:
            allv = [v for _, vals in series for v in vals] or [0]
            lo, hi = min(0, min(allv)), max(0, max(allv))
        span = (hi - lo) or 1
        ax.set_ylim(lo - (span * 0.04 if lo < 0 else 0), hi + span * 0.18)
        ax.yaxis.set_major_locator(MaxNLocator(nbins=4))
        ax.yaxis.set_major_formatter(FuncFormatter(compact_inr))
        ax.grid(axis="y", color=p.grid, linewidth=0.8, linestyle="-")
        ax.set_axisbelow(True)
        ax.axhline(0, color=p.grid, linewidth=1)
        self.fig.tight_layout(pad=0.8)
        xpp, ypp = self._px(ax)
        k = 1 if stacked else len(series)
        slot = 0.8
        bar_w = min(slot / k - 2 * xpp, 24 * xpp)
        gap = 2 * xpp
        for i in range(n):
            if stacked:
                base = 0.0
                for si, (name, vals) in enumerate(series):
                    v = max(vals[i], 0)
                    if v <= 0:
                        continue
                    top = base + v
                    is_top = all(max(series[sj][1][i], 0) <= 0 for sj in range(si + 1, len(series)))
                    if is_top:
                        self._rounded_bar(ax, i - bar_w / 2, bar_w, base, top - (gap * ypp / xpp if si else 0), cols[si % 2])
                    else:
                        ax.add_patch(Rectangle((i - bar_w / 2, base), bar_w, v - 2 * ypp, linewidth=0, facecolor=cols[si % 2]))
                    base = top
                tot = sum(max(s[1][i], 0) for s in series)
                self._hits.append((i - 0.5, i + 0.5, labels[i], [(n_, s[1][i]) for n_, s in zip([s[0] for s in series], series)], tot))
            else:
                group_w = k * bar_w + (k - 1) * gap
                for si, (name, vals) in enumerate(series):
                    x = i - group_w / 2 + si * (bar_w + gap)
                    self._rounded_bar(ax, x, bar_w, 0.0, vals[i], cols[si % 2])
                self._hits.append((i - 0.5, i + 0.5, labels[i], [(s[0], s[1][i]) for s in series], max([s[1][i] for s in series])))
        ax.set_xticks(range(n))
        step = max(1, n // 8)
        ax.set_xticklabels([lb if idx % step == 0 or idx == n - 1 else "" for idx, lb in enumerate(labels)])
        # legend handles for multi-series
        if len(series) > 1:
            for si, (name, _) in enumerate(series):
                ax.bar([0], [0], color=cols[si % 2], label=name, width=0)
        # selective direct label: the highest column only (single series)
        if len(series) == 1 and n:
            vals = series[0][1]
            mi = max(range(n), key=lambda j: vals[j])
            if vals[mi] > 0:
                ax.annotate(compact_inr(vals[mi]), (mi, vals[mi]), xytext=(0, 4), textcoords="offset points",
                            ha="center", va="bottom", color=p.text2, fontsize=8, fontweight="bold")

    def _render_hbar(self, ax, labels, series):
        p = current()
        name, vals = series
        n = len(labels)
        order = list(range(n))[::-1]
        ax.set_ylim(-0.6, n - 0.4)
        hi = max(vals + [0]) or 1
        ax.set_xlim(0, hi * 1.22)
        ax.xaxis.set_major_locator(MaxNLocator(nbins=4))
        ax.xaxis.set_major_formatter(FuncFormatter(compact_inr))
        ax.grid(axis="x", color=p.grid, linewidth=0.8, linestyle="-")
        ax.set_axisbelow(True)
        ax.spines["bottom"].set_visible(False)
        ax.spines["left"].set_visible(True)
        ax.spines["left"].set_color(p.grid)
        self.fig.tight_layout(pad=0.8)
        xpp, ypp = self._px(ax)
        bar_h = min(0.7, 18 * ypp)
        col = self._colors()[0]
        for pos, i in enumerate(order):
            v = vals[i]
            if v > 0:
                r = min(4 * xpp, v / 2)
                ax.add_patch(FancyBboxPatch((0, pos - bar_h / 2), v, bar_h, boxstyle=f"round,pad=0,rounding_size={r}",
                                            mutation_aspect=ypp / xpp, linewidth=0, facecolor=col))
                ax.add_patch(Rectangle((0, pos - bar_h / 2), min(v, r), bar_h, linewidth=0, facecolor=col))
                ax.annotate(compact_inr(v), (v, pos), xytext=(4, 0), textcoords="offset points", va="center",
                            ha="left", color=p.text2, fontsize=7.5)
            self._hits.append((pos - 0.5, pos + 0.5, labels[i], [(name, v)], v, "h"))
        ax.set_yticks(range(n))
        ax.set_yticklabels([(labels[i][:26] + "…") if len(labels[i]) > 27 else labels[i] for i in order], fontsize=7.8)

    def _render_lines(self, ax, labels, series):
        p = current()
        cols = self._colors()
        n = len(labels)
        xs = list(range(n))
        allv = [v for _, vals in series for v in vals] or [0]
        lo, hi = min(0, min(allv)), max(0, max(allv))
        span = (hi - lo) or 1
        ax.set_ylim(lo - span * 0.05, hi + span * 0.2)
        ax.set_xlim(-0.3, max(n - 0.7, 0.3))
        ax.yaxis.set_major_locator(MaxNLocator(nbins=4))
        ax.yaxis.set_major_formatter(FuncFormatter(compact_inr))
        ax.grid(axis="y", color=p.grid, linewidth=0.8, linestyle="-")
        ax.set_axisbelow(True)
        if lo < 0:
            ax.axhline(0, color=p.muted, linewidth=0.8)
        for si, (name, vals) in enumerate(series):
            c = cols[si % 2]
            ax.plot(xs, vals, color=c, linewidth=2, solid_capstyle="round", solid_joinstyle="round", label=name)
            if n:
                ax.plot([xs[-1]], [vals[-1]], marker="o", markersize=8, color=c, markeredgecolor=p.card,
                        markeredgewidth=2, zorder=5)
                ax.annotate(compact_inr(vals[-1]), (xs[-1], vals[-1]), xytext=(6, 0), textcoords="offset points",
                            va="center", color=p.text2, fontsize=7.8, fontweight="bold")
        if len(series) == 1 and n:
            ax.fill_between(xs, series[0][1], [0] * n, color=cols[0], alpha=0.10, linewidth=0)
        ax.set_xticks(xs)
        step = max(1, n // 8)
        ax.set_xticklabels([lb if idx % step == 0 or idx == n - 1 else "" for idx, lb in enumerate(labels)])
        for i in range(n):
            self._hits.append((i - 0.5, i + 0.5, labels[i], [(s[0], s[1][i]) for s in series], max(s[1][i] for s in series), "line"))

    # ---------------------------------------------------------------- hover layer
    def _hide_tip(self):
        changed = False
        if self._tip is not None:
            self._tip.set_visible(False)
            changed = True
        if self._cross is not None:
            self._cross.set_visible(False)
            changed = True
        if changed:
            self.canvas.draw_idle()

    def _on_move(self, event):
        if not self._hits or event.inaxes is None:
            self._hide_tip()
            return
        horizontal = self._spec and self._spec["kind"] == "hbar"
        coord = event.ydata if horizontal else event.xdata
        if coord is None:
            return
        hit = next((h for h in self._hits if h[0] <= coord < h[1]), None)
        if hit is None:
            self._hide_tip()
            return
        p = current()
        lines = [hit[2]] + [f"{n}: {inr(Decimal(str(round(v, 2))))}" for n, v in hit[3]]
        text = "\n".join(lines)
        ax = event.inaxes
        center = (hit[0] + hit[1]) / 2
        xy = (hit[4], center) if horizontal else (center, hit[4])
        if self._tip is None:
            self._tip = ax.annotate(text, xy=xy, xytext=(0, 12), textcoords="offset points", ha="center", va="bottom",
                                    fontsize=8, color=p.card, zorder=10,
                                    bbox=dict(boxstyle="round,pad=0.45", fc=p.text, ec="none", alpha=0.95))
        self._tip.set_text(text)
        self._tip.xy = xy
        if horizontal:
            self._tip.set_ha("left")
            self._tip.set_position((10, 0))
            self._tip.set_va("center")
        else:
            x0, x1 = ax.get_xlim()
            frac = (center - x0) / ((x1 - x0) or 1)
            self._tip.set_ha("right" if frac > 0.75 else ("left" if frac < 0.2 else "center"))
        self._tip.set_visible(True)
        if len(hit) > 5 and hit[5] == "line":
            if self._cross is None:
                self._cross = ax.axvline(center, color=p.muted, linewidth=1, zorder=1)
            self._cross.set_xdata([center, center])
            self._cross.set_visible(True)
        self.canvas.draw_idle()
