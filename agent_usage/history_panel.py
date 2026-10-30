"""팝업 옆에 붙는 사용 기록 패널: 서비스마다 미니 그래프 하나 (사용한 비율, 0~100%).

패널은 테두리 없는 창으로 팝업 왼쪽(자리가 없으면 오른쪽)에 붙고, 팝업과 높이를 맞춘다.
각 서비스 구역은 팝업의 같은 서비스 카드와 같은 높이에 놓여서 나란히 읽힌다.
"""
from __future__ import annotations

import bisect
import time
import tkinter as tk
from datetime import datetime

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
        cards = self.popup.card_frames()
        top_y = self.head.winfo_y() + self.head.winfo_height() + self.px(4)   # 패널 머리글 아래
        aligned = cards and not self.popup.scrolling and all(pid in cards for pid in self.sections)
        if aligned:
            # 두 창은 위쪽 높이가 같으므로 팝업 창 기준으로 잰다 (막 옮긴 패널 자신의 위치는 아직 옛값일 수 있다).
            # -1: 패널 프레임은 테두리 1px 안쪽에서 시작한다
            base = self.popup.win.winfo_rooty() + 1
            for pid, canvas in self.sections.items():
                card = cards[pid]
                y = card.winfo_rooty() - base
                canvas.place(x=self.px(10), y=max(y, top_y), relwidth=1, width=-self.px(20),
                             height=card.winfo_height() - max(0, top_y - y))
        else:      # 팝업이 화면보다 길어 스크롤될 때는 카드에 맞추지 않고 머리글 아래를 고르게 나눈다
            n = max(1, len(self.sections))
            each = max(self.px(90), (height - top_y - self.px(8)) // n)
            for i, canvas in enumerate(self.sections.values()):
                canvas.place(x=self.px(10), y=top_y + i * each, relwidth=1, width=-self.px(20),
                             height=each - self.px(8))
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
        rows = max([row for *_, row in self._legend_layout(c)] or [0]) + 1
        top = self.px(14) + rows * self.px(14)                         # 실제 범례 줄 수만큼 비운다
        return self.px(30), top, w - self.px(10), h - self.px(18)      # 왼, 위, 오른, 아래

    def _legend_layout(self, c: tk.Canvas) -> list[tuple[int, str, str, int]]:
        """범례 배치 [(x, 글자, 색, 줄)]: 제목 오른쪽부터, 넘치면 다음 줄로."""
        px, out = self.px, []
        x, row = px(70), 0
        for window, pts in self.data.get(c.pid, {}).items():
            text = f"{pts[-1][2]} {pts[-1][1]:.0f}%"
            width = len(text) * px(6) + px(16)
            if x + width > c.winfo_width() - px(6) and x > px(8) and (row > 0 or x > px(70)):
                x, row = px(8), row + 1
            out.append((x, text, theme.series_color(self.pal, c.pid, window), row))
            x += width
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
        for x, text, color, row in self._legend_layout(c):
            y = px(10) + row * px(14)
            c.create_line(x, y, x + px(10), y, fill=color, width=px(2))
            c.create_text(x + px(13), y, text=text, anchor="w", font=small, fill=pal["text2"])
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
        lh, tw = px(14), max(len(s) for s in lines) * px(6) + px(24)
        th = lh * len(lines) + px(6)
        x = e.x + px(10) if e.x + px(10) + tw < right else e.x - px(10) - tw
        y = top
        c.create_rectangle(x, y, x + tw, y + th, fill=pal["bg"], outline=pal["border"], tags="hover")
        for i, s in enumerate(lines):
            ty = y + px(3) + lh * i + lh / 2
            if i:
                c.create_rectangle(x + px(6), ty - px(3), x + px(12), ty + px(3), fill=rows[i - 1][2],
                                   outline="", tags="hover")
            c.create_text(x + px(16 if i else 6), ty, text=s, anchor="w", tags="hover",
                          font=(theme.FONT, 8, "bold") if not i else (theme.FONT, 8), fill=pal["text"])
