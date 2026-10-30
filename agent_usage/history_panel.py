"""팝업 옆에 붙는 사용 기록 패널: 서비스마다 미니 그래프 하나 (사용한 비율, 0~100%).

패널은 테두리 없는 창으로 팝업 왼쪽(자리가 없으면 오른쪽)에 붙고, 팝업과 높이를 맞춘다.
그래프는 모두 같은 크기(사용량이 1줄인 카드 높이)이고, 각 서비스 카드의 위쪽에 맞춰 놓여 나란히 읽힌다.
"""
from __future__ import annotations

import bisect
import re
import time
import tkinter as tk
from datetime import datetime
from tkinter import font as tkfont

from . import theme, winutil
from .i18n import t
from .providers import BY_ID

RANGES = [("g_24h", 24), ("g_7d", 168), ("g_30d", 720)]
GAP_BREAK_S = 2 * 3600      # 이보다 오래 기록이 비면(PC 꺼짐 등) 선을 끊는다
WIDTH = 300
GAP = 8                     # 팝업과 패널 사이 간격


def panel_x(popup_x: int, popup_w: int, panel_w: int, gap: int, area_left: int, area_right: int) -> int:
    """기본은 팝업 왼쪽, 화면 왼쪽 끝에 걸리면 오른쪽에 붙인다."""
    left = popup_x - gap - panel_w
    if left >= area_left:
        return left
    right = popup_x + popup_w + gap
    return right if right + panel_w <= area_right else max(area_left, left)


def uniform_height(cards: list[tuple[int, int]], default: int) -> int:
    """모든 그래프에 쓸 높이: 사용량이 1줄인 카드 중 가장 낮은 높이 (없으면 기본값). cards=[(높이, 줄 수)]"""
    singles = [h for h, rows in cards if rows == 1 and h > 1]
    return min(singles) if singles else default


def chart_height(cards: list[tuple[int, int]], default: int, floor: int) -> tuple[int, bool]:
    """(그래프 높이, 카드에 맞춰 놓을 수 있는지).

    오류 카드처럼 그보다 낮은 카드가 있으면 크기를 맞추려고 모두 그 높이로 줄이고,
    그마저 floor보다 낮으면 카드에 맞추지 않고 원래 높이로 쌓는다.
    """
    h = uniform_height(cards, default)
    shortest = min((ch for ch, _ in cards if ch > 1), default=h)
    if shortest >= h:
        return h, True
    return (shortest, True) if shortest >= floor else (h, False)


def stack_height(h: int, count: int, space: int, gap: int) -> int:
    """쌓을 때 그래프 높이: 패널 안(space)에 다 들어가게 필요하면 모두 같이 줄인다."""
    if count <= 0:
        return h
    return max(1, min(h, (space - gap * (count - 1)) // count))


_SUFFIX = re.compile(r"\s*\([^()]*\)$")


def fit_legend(labels: list[str], width: int, measure, extra: int) -> tuple[list[str], int]:
    """범례를 한 줄에 맞춘다: (보여줄 이름들, 숨긴 개수).

    measure(글자) → 픽셀 폭, extra = 항목마다 붙는 선 표시·간격 폭.
    넘치면 ① 모든 이름에 같은 꼬리표("(하루)" 등)가 있을 때 떼고 ② 그래도 넘치면 뒤에서부터 빼고 "+N"을 붙인다.
    아주 좁으면 이름 없이 "+N"만, 그마저 안 들어가면 범례를 비운다.
    """
    def total(items, hidden=0):
        return sum(measure(s) + extra for s in items) + (measure(f"+{hidden}") if hidden else 0)

    if total(labels) <= width:
        return labels, 0
    tails = {(_SUFFIX.search(s) or [None])[0] for s in labels}
    if len(labels) > 1 and len(tails) == 1 and None not in tails:
        labels = [_SUFFIX.sub("", s) for s in labels]
        if total(labels) <= width:
            return labels, 0
    shown = list(labels)
    while shown and total(shown, len(labels) - len(shown)) > width:
        shown.pop()
    if not shown and total([], len(labels)) > width:
        return [], 0
    return shown, len(labels) - len(shown)


def group_series(series: dict, visible: list[str]) -> dict[str, dict[str, list]]:
    """{(서비스, 한도): [(ts, used, label)]} → {서비스: {한도: [...]}} (보이는 서비스만)."""
    out: dict[str, dict[str, list]] = {}
    for (provider, window), pts in series.items():
        if provider in visible and pts:
            out.setdefault(provider, {})[window] = pts
    return out


class HistoryPanel:
    def __init__(self, popup):
        self.popup = popup
        self.app = popup.app
        self.pal = popup.app.pal
        self.px = popup.px
        self.win: tk.Toplevel | None = None
        self.visible = False
        self.sections: dict[str, tk.Canvas] = {}
        self.data: dict[str, dict[str, list]] = {}
        self.now = time.time()

    @property
    def hours(self) -> int:
        return self.app.settings["history_range_hours"] or 24

    # ── 보이기/숨기기·위치 ──────────────────────────────────
    def _create(self) -> None:
        pal = self.pal
        self.win = tk.Toplevel(self.app.root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=pal["border"])
        self.frame = tk.Frame(self.win, bg=pal["bg"])
        self.frame.pack(fill="both", expand=True, padx=1, pady=1)
        self.win.bind("<Escape>", lambda e: self.popup.hide())
        # 패널을 눌러도 팝업이 '포커스를 잃었다'며 닫히지 않게 포커스를 이 앱 안에 둔다
        self.win.bind("<ButtonPress>", lambda e: self.win.focus_force(), add="+")
        # 패널에서 다른 앱으로 넘어가도 팝업과 똑같이 닫히게 (고정하지 않았을 때)
        self.win.bind("<FocusOut>", lambda e: self.popup.win.after(150, self.popup._check_focus))
        self._build_header()

    def show(self) -> None:
        if not self.win:
            self._create()
        self.visible = True
        self.redraw()
        self.win.deiconify()
        self.win.lift()
        winutil.round_corners(self.win)
        self.win.after(50, self.follow)      # 화면에 나타난 뒤 한 번 더 맞춘다

    def hide(self) -> None:
        if self.win and self.visible:
            self.win.withdraw()
            self.visible = False

    def alive(self) -> bool:
        return bool(self.win) and self.visible

    def follow(self) -> None:
        """팝업 위치·높이에 맞춰 옮기고, 서비스 구역을 팝업 카드 높이에 맞춘다."""
        if not (self.visible and self.popup.win):
            return
        pw = self.popup.win
        pw.update_idletasks()
        x0, y0, w0, h0 = pw.winfo_x(), pw.winfo_y(), pw.winfo_width(), pw.winfo_height()
        left, top, right, bottom = winutil.work_area_at(x0 + w0 // 2, y0)
        w = self.px(WIDTH)
        x = panel_x(x0, w0, w, self.px(GAP), left, right)
        self.win.geometry(f"{w}x{h0}+{x}+{y0}")
        self._layout_sections(h0)

    def _layout_sections(self, height: int) -> None:
        self.frame.update_idletasks()
        layout = self.popup.card_layout()
        top_y = self.head.winfo_y() + self.head.winfo_height() + self.px(4)   # 패널 머리글 아래
        # 그래프 크기는 모두 같게: 사용량이 1줄인 카드 높이 (Claude처럼 줄이 많은 카드에 맞춰 커지지 않게)
        chart_h, fits = chart_height([(f.winfo_height(), rows) for f, rows in layout.values()],
                                     self.px(140), self.px(64))
        aligned = fits and layout and not self.popup.scrolling and all(pid in layout for pid in self.sections)
        if aligned:
            # 각 그래프 위쪽을 그 서비스 카드 위쪽에 맞춘다. 두 창은 위쪽 높이가 같으므로 팝업 창 기준으로 잰다
            # (막 옮긴 패널 자신의 위치는 아직 옛값일 수 있다). +1: 패널 프레임은 테두리 1px 안쪽에서 시작한다
            base = self.popup.win.winfo_rooty() + 1
            for pid, canvas in self.sections.items():
                card = layout[pid][0]
                y = max(card.winfo_rooty() - base, top_y)
                canvas.place(x=self.px(10), y=y, relwidth=1, width=-self.px(20), height=chart_h)
        else:      # 팝업이 화면보다 길어 스크롤될 때 등은 카드에 맞추지 않고 머리글 아래부터 차례로 쌓는다
            gap = self.px(8)
            chart_h = stack_height(chart_h, len(self.sections), height - top_y - self.px(10), gap)
            for i, canvas in enumerate(self.sections.values()):
                canvas.place(x=self.px(10), y=top_y + i * (chart_h + gap), relwidth=1, width=-self.px(20),
                             height=chart_h)
        for canvas in self.sections.values():
            canvas.update_idletasks()
            self._draw(canvas)

    # ── 머리글 ───────────────────────────────────────────
    def _build_header(self) -> None:
        pal, px = self.pal, self.px
        head = tk.Frame(self.frame, bg=pal["bg"])
        head.pack(fill="x", padx=(px(14), px(10)), pady=(px(8), px(2)))    # 팝업 머리글과 같은 높이
        self.head = head
        tk.Label(head, text=t("h_title"), font=(theme.FONT, 11, "bold"), fg=pal["text"],
                 bg=pal["bg"]).pack(side="left")
        tk.Label(head, text=t("h_used"), font=(theme.FONT, 8), fg=pal["muted"],
                 bg=pal["bg"]).pack(side="left", padx=(px(6), 0), pady=(px(3), 0))
        self.range_buttons = {}
        for key, hours in reversed(RANGES):
            b = tk.Label(head, text=t(key), font=(theme.FONT, 8), padx=px(6), pady=px(2), cursor="hand2")
            b.bind("<Button-1>", lambda e, h=hours: self._set_range(h))
            b.pack(side="right")
            self.range_buttons[hours] = b
        self._style_range()

    def _style_range(self) -> None:
        for hours, b in self.range_buttons.items():
            on = hours == self.hours
            b.configure(bg=self.pal["hover"] if on else self.pal["bg"],
                        fg=self.pal["text"] if on else self.pal["text2"],
                        font=(theme.FONT, 8, "bold") if on else (theme.FONT, 8))

    def _set_range(self, hours: int) -> None:
        self.app.settings["history_range_hours"] = hours
        self._style_range()
        self.redraw()

    # ── 데이터·그리기 ──────────────────────────────────────
    def redraw(self) -> None:
        if not self.win:
            return
        self.now = time.time()
        self.data = group_series(self.app.history.series(self.hours), self.app.visible)
        wanted = [pid for pid in self.app.visible]
        if list(self.sections) != wanted:
            for canvas in self.sections.values():
                canvas.destroy()
            self.sections = {}
            for pid in wanted:
                c = tk.Canvas(self.frame, bg=self.pal["card"], highlightthickness=1,
                              highlightbackground=self.pal["border"])
                c.pid = pid
                c.bind("<Configure>", lambda e, c=c: self._draw(c))
                c.bind("<Motion>", lambda e, c=c: self._hover(c, e))
                c.bind("<Leave>", lambda e, c=c: c.delete("hover"))
                self.sections[pid] = c
        self.follow()

    def _geometry(self, c: tk.Canvas):
        w, h = c.winfo_width(), c.winfo_height()
        return self.px(30), self.px(28), w - self.px(10), h - self.px(18)      # 왼, 위(제목·범례 한 줄), 오른, 아래

    def _fonts(self):
        if not hasattr(self, "_title_font"):      # 글자 폭을 정확히 재려고 실제 글꼴을 쓴다
            self._title_font = tkfont.Font(family=theme.FONT, size=9, weight="bold")
            self._small_font = tkfont.Font(family=theme.FONT, size=7)
            self._tip_bold = tkfont.Font(family=theme.FONT, size=8, weight="bold")
            self._tip_font = tkfont.Font(family=theme.FONT, size=8)
        return self._title_font, self._small_font, self._tip_bold, self._tip_font

    def _ordered_windows(self, pid: str) -> list[str]:
        """범례 순서는 팝업 카드의 한도 순서를 따른다."""
        series = self.data.get(pid, {})
        good = self.app.good.get(pid)
        order = [w.key for w in good.windows] if good else []
        return sorted(series, key=lambda k: order.index(k) if k in order else len(order))

    def _legend_layout(self, c: tk.Canvas) -> list[tuple[int, str, str | None]]:
        """범례 배치 [(x, 글자, 색)]: 제목 오른쪽에 이름만 한 줄로. 넘치면 '+N'(색 없음)."""
        px = self.px
        title_font, small = self._fonts()[:2]
        mod = BY_ID.get(c.pid)
        x0 = px(8) + title_font.measure(mod.TITLE if mod else c.pid) + px(10)
        series = self.data.get(c.pid, {})
        windows = self._ordered_windows(c.pid)
        labels = [series[w][-1][2] for w in windows]
        marker = px(16)                               # 선 표시 + 간격
        shown, hidden = fit_legend(labels, c.winfo_width() - x0 - px(6), small.measure, marker + px(6))
        out, x = [], x0
        for window, text in zip(windows, shown):
            out.append((x, text, theme.series_color(self.pal, c.pid, window)))
            x += small.measure(text) + marker + px(6)
        if hidden:
            out.append((x, f"+{hidden}", None))
        return out

    def _x(self, c: tk.Canvas, ts: float) -> float:
        left, _, right, _ = self._geometry(c)
        start = self.now - self.hours * 3600
        return left + (right - left) * (ts - start) / (self.hours * 3600)

    def _y(self, c: tk.Canvas, used: float) -> float:
        _, top, _, bottom = self._geometry(c)
        return bottom - (bottom - top) * max(0.0, min(100.0, used)) / 100

    def _draw(self, c: tk.Canvas) -> None:
        pal, px = self.pal, self.px
        c.delete("all")
        if c.winfo_width() < 60 or c.winfo_height() < 50:
            return
        left, top, right, bottom = self._geometry(c)
        small = (theme.FONT, 7)
        mod = BY_ID.get(c.pid)
        c.create_text(px(8), px(4), text=mod.TITLE if mod else c.pid, anchor="nw",
                      font=(theme.FONT, 9, "bold"), fill=pal["text"])
        legend_y = px(11)
        for x, text, color in self._legend_layout(c):
            if color is None:                           # 다 못 넣은 항목 수
                c.create_text(x, legend_y, text=text, anchor="w", font=small, fill=pal["muted"])
                continue
            c.create_line(x, legend_y, x + px(10), legend_y, fill=color, width=px(2))
            c.create_text(x + px(13), legend_y, text=text, anchor="w", font=small, fill=pal["text2"])
        for v in (0, 50, 100):
            y = self._y(c, v)
            c.create_line(left, y, right, y, fill=pal["axis"] if v == 0 else pal["grid"])
            if v != 50:
                c.create_text(left - px(4), y, text=f"{v}", anchor="e", font=small, fill=pal["muted"])
        for ts, text in self._ticks():
            c.create_text(self._x(c, ts), bottom + px(3), text=text, anchor="n", font=small, fill=pal["muted"])
        series = self.data.get(c.pid, {})
        if not series:
            c.create_text((left + right) / 2, (top + bottom) / 2, text=t("h_empty"), font=small, fill=pal["muted"])
            return
        for window, pts in series.items():
            color = theme.series_color(pal, c.pid, window)
            seg = []
            for i, (ts, used, _) in enumerate(pts):
                if i and ts - pts[i - 1][0] > GAP_BREAK_S:
                    self._line(c, seg, color)
                    seg = []
                seg += [self._x(c, ts), self._y(c, used)]
            self._line(c, seg, color)

    def _line(self, c: tk.Canvas, seg: list, color: str) -> None:
        if len(seg) >= 4:
            c.create_line(*seg, fill=color, width=self.px(2), joinstyle="round", capstyle="round")
        elif len(seg) == 2:
            r = self.px(2)
            c.create_oval(seg[0] - r, seg[1] - r, seg[0] + r, seg[1] + r, fill=color, outline="")

    def _ticks(self) -> list[tuple[float, str]]:
        """칸이 좁아서 눈금은 셋만: 시작·가운데·끝."""
        start = self.now - self.hours * 3600
        out = []
        for frac in (0.0, 0.5, 1.0):
            ts = start + frac * self.hours * 3600
            d = datetime.fromtimestamp(ts)
            text = t("g_hour", h=d.hour) if self.hours <= 24 else f"{d.month}/{d.day}"
            out.append((ts, text))
        return out[1:]    # 맨 왼쪽 눈금은 y축 숫자와 겹쳐서 뺀다

    def _hover(self, c: tk.Canvas, e) -> None:
        pal, px = self.pal, self.px
        c.delete("hover")
        series = self.data.get(c.pid, {})
        left, top, right, bottom = self._geometry(c)
        if not series or not left <= e.x <= right:
            return
        ts = self.now - self.hours * 3600 + (e.x - left) / (right - left) * self.hours * 3600
        rows = []
        for window, pts in series.items():
            i = bisect.bisect_right([p[0] for p in pts], ts) - 1
            if i >= 0 and ts - pts[i][0] <= GAP_BREAK_S:
                rows.append((pts[i][2], pts[i][1], theme.series_color(pal, c.pid, window)))
        c.create_line(e.x, top, e.x, bottom, fill=pal["axis"], tags="hover")
        if not rows:
            return
        d = datetime.fromtimestamp(ts)
        lines = [f"{d.month}/{d.day} {d:%H:%M}"] + [f"{label} {used:.0f}%" for label, used, _ in rows]
        # 그래프 칸 안에 다 들어가게: 줄이 넘치면 뒤쪽 항목은 "+N"으로 묶는다
        lh, cw, ch = px(14), c.winfo_width(), c.winfo_height()
        fit = max(2, (ch - px(10)) // lh)
        more = len(lines) > fit
        if more:
            lines = lines[:fit - 1] + [f"+{len(lines) - fit + 1}"]
        bold, regular = self._fonts()[2:]
        tw = max([bold.measure(lines[0])] + [regular.measure(s) + px(10) for s in lines[1:]]) + px(14)
        th = lh * len(lines) + px(6)
        x = e.x + px(10) if e.x + px(10) + tw < right else e.x - px(10) - tw
        x = max(px(2), min(x, cw - tw - px(2)))
        y = max(px(2), min(top, ch - th - px(2)))
        c.create_rectangle(x, y, x + tw, y + th, fill=pal["bg"], outline=pal["border"], tags="hover")
        for i, s in enumerate(lines):
            ty = y + px(3) + lh * i + lh / 2
            row = 0 < i and not (more and i == len(lines) - 1)
            if row:
                c.create_rectangle(x + px(6), ty - px(3), x + px(12), ty + px(3), fill=rows[i - 1][2],
                                   outline="", tags="hover")
            c.create_text(x + px(16 if row else 6), ty, text=s, anchor="w", tags="hover",
                          font=bold if not i else regular, fill=pal["text"] if row or not i else pal["muted"])
