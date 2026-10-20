"""사용 기록 그래프 창. 서비스·한도별 '사용한 비율'을 시간에 따라 선으로 그린다."""
from __future__ import annotations

import bisect
import time
import tkinter as tk
from datetime import datetime
from tkinter import ttk

from . import theme, winutil
from .i18n import t
from .providers import BY_ID

RANGES = [("g_24h", 24), ("g_7d", 168), ("g_30d", 720)]
GAP_BREAK_S = 2 * 3600     # 이보다 오래 기록이 비면(PC 꺼짐 등) 선을 끊는다


class HistoryWindow:
    def __init__(self, app):
        self.app = app
        self.pal = pal = app.pal
        self.px = lambda v: int(round(v * app.scale))
        self.hours = app.settings["history_range_hours"] or 168
        self.table_mode = False
        self.series: dict = {}

        self.win = tk.Toplevel(app.root)
        self.win.title(t("g_title"))
        self.win.configure(bg=pal["card"])
        self.win.geometry(f"{self.px(760)}x{self.px(420)}")
        self.win.minsize(self.px(520), self.px(300))

        bar = tk.Frame(self.win, bg=pal["card"])
        bar.pack(fill="x", padx=self.px(16), pady=(self.px(12), self.px(4)))
        tk.Label(bar, text=t("g_heading"), font=(theme.FONT, 11, "bold"), fg=pal["text"],
                 bg=pal["card"]).pack(side="left")
        tk.Label(bar, text="  " + t("g_sub"), font=(theme.FONT, 9), fg=pal["muted"],
                 bg=pal["card"]).pack(side="left", pady=(self.px(2), 0))
        self.buttons = {}
        self._button(bar, t("g_table"), self._toggle_table, key="table").pack(side="right", padx=(self.px(10), 0))
        for label, hours in reversed(RANGES):
            self._button(bar, t(label), lambda h=hours: self._set_range(h), key=hours).pack(side="right")

        self.legend = tk.Frame(self.win, bg=pal["card"])
        self.legend.pack(fill="x", padx=self.px(16), pady=(0, self.px(4)))
        self.canvas = tk.Canvas(self.win, bg=pal["card"], highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=self.px(8), pady=(0, self.px(10)))
        self.canvas.bind("<Configure>", lambda e: self.redraw())
        self.canvas.bind("<Motion>", self._hover)
        self.canvas.bind("<Leave>", lambda e: self.canvas.delete("hover"))
        self.table = None
        self._style_buttons()
        winutil.bring_to_front(self.win)

    # ── 공통 ─────────────────────────────────────────────
    def alive(self) -> bool:
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def focus(self) -> None:
        winutil.bring_to_front(self.win)

    def _button(self, parent, text, command, key) -> tk.Label:
        b = tk.Label(parent, text=text, font=(theme.FONT, 9), padx=self.px(10), pady=self.px(3), cursor="hand2")
        b.bind("<Button-1>", lambda e: command())
        self.buttons[key] = b
        return b

    def _style_buttons(self) -> None:
        pal = self.pal
        for key, b in self.buttons.items():
            on = key == self.hours or (key == "table" and self.table_mode)
            b.configure(bg=pal["hover"] if on else pal["card"], fg=pal["text"] if on else pal["text2"],
                        font=(theme.FONT, 9, "bold") if on else (theme.FONT, 9))

    def _set_range(self, hours: int) -> None:
        self.hours = hours
        self.app.settings["history_range_hours"] = hours
        self._style_buttons()
        self.redraw()

    def _toggle_table(self) -> None:
        self.table_mode = not self.table_mode
        self._style_buttons()
        self.redraw()

    def _name(self, key) -> str:
        provider, _ = key
        label = self.series[key][-1][2]
        mod = BY_ID.get(provider)
        return f"{mod.TITLE if mod else provider} {label}"

    def _color(self, key) -> str:
        return theme.series_color(self.pal, *key)

    # ── 그리기 ─────────────────────────────────────────────
    def redraw(self) -> None:
        self.series = {k: v for k, v in self.app.history.series(self.hours).items() if k[0] in self.app.visible}
        self._draw_legend()
        if self.table_mode:
            self.canvas.pack_forget()
            self._draw_table()
        else:
            if self.table:
                self.table.destroy()
                self.table = None
            self.canvas.pack(fill="both", expand=True, padx=self.px(8), pady=(0, self.px(10)))
            self._draw_chart()

    def _draw_legend(self) -> None:
        for w in self.legend.winfo_children():
            w.destroy()
        if len(self.series) < 2:
            return
        for i, key in enumerate(self._ordered_keys()):   # 한 줄에 4개씩
            item = tk.Frame(self.legend, bg=self.pal["card"])
            item.grid(row=i // 4, column=i % 4, sticky="w", padx=(0, self.px(14)), pady=(0, self.px(2)))
            sw = tk.Canvas(item, width=self.px(14), height=self.px(10), bg=self.pal["card"], highlightthickness=0)
            sw.create_line(0, self.px(5), self.px(14), self.px(5), width=self.px(2), fill=self._color(key))
            sw.pack(side="left", padx=(0, self.px(4)))
            tk.Label(item, text=self._name(key), font=(theme.FONT, 9), fg=self.pal["text2"],
                     bg=self.pal["card"]).pack(side="left")

    def _ordered_keys(self):
        return sorted(self.series, key=lambda k: theme.series_slot(*k))

    def _geometry(self):
        c = self.canvas
        w, h = c.winfo_width(), c.winfo_height()
        right_pad = self.px(110) if len(self.series) <= 4 else self.px(16)
        return self.px(44), self.px(12), w - right_pad, h - self.px(26)   # 왼, 위, 오른, 아래

    def _x(self, ts: float) -> float:
        left, _, right, _ = self._geometry()
        start = self.now - self.hours * 3600
        return left + (right - left) * (ts - start) / (self.hours * 3600)

    def _y(self, used: float) -> float:
        _, top, _, bottom = self._geometry()
        return bottom - (bottom - top) * max(0.0, min(100.0, used)) / 100

    def _draw_chart(self) -> None:
        c, pal = self.canvas, self.pal
        c.delete("all")
        self.now = time.time()
        left, top, right, bottom = self._geometry()
        if right - left < 50:
            return
        small = (theme.FONT, 8)

        for v in (0, 25, 50, 75, 100):
            y = self._y(v)
            c.create_line(left, y, right, y, fill=pal["axis"] if v == 0 else pal["grid"], width=1)
            c.create_text(left - self.px(6), y, text=f"{v}%", anchor="e", font=small, fill=pal["muted"])
        for ts, text in self._ticks():
            x = self._x(ts)
            c.create_line(x, bottom, x, bottom + self.px(4), fill=pal["axis"])
            c.create_text(x, bottom + self.px(6), text=text, anchor="n", font=small, fill=pal["muted"])

        if not self.series:
            c.create_text((left + right) / 2, (top + bottom) / 2, font=(theme.FONT, 10), fill=pal["muted"],
                          text=t("g_empty"))
            return

        ends = []
        for key in self._ordered_keys():
            pts = self.series[key]
            seg = []
            for i, (ts, used, _) in enumerate(pts):
                if i and ts - pts[i - 1][0] > GAP_BREAK_S:
                    self._polyline(seg, key)
                    seg = []
                seg += [self._x(ts), self._y(used)]
            self._polyline(seg, key)
            ends.append([self._y(pts[-1][1]), key])

        if len(self.series) <= 4:   # 선 끝 이름표 (겹치지 않게 아래로 민다)
            ends.sort()
            gap = self.px(14)
            for i in range(1, len(ends)):
                ends[i][0] = max(ends[i][0], ends[i - 1][0] + gap)
            for y, key in ends:
                c.create_text(right + self.px(8), y, anchor="w", font=small, fill=pal["text2"],
                              text=f"{self._name(key)} {self.series[key][-1][1]:.0f}%")

    def _polyline(self, seg: list, key) -> None:
        if len(seg) >= 4:
            self.canvas.create_line(*seg, fill=self._color(key), width=self.px(2), joinstyle="round", capstyle="round")
        elif len(seg) == 2:
            r = self.px(2)
            self.canvas.create_oval(seg[0] - r, seg[1] - r, seg[0] + r, seg[1] + r, fill=self._color(key), outline="")

    def _ticks(self):
        start = self.now - self.hours * 3600
        if self.hours <= 24:
            step, fmt = 4 * 3600, lambda d: t("g_hour", h=d.hour)
        elif self.hours <= 168:
            step, fmt = 86400, lambda d: f"{d.month}/{d.day}"
        else:
            step, fmt = 5 * 86400, lambda d: f"{d.month}/{d.day}"
        # 현지 시각 기준 정각/자정에 맞춘다
        first = datetime.fromtimestamp(start).replace(minute=0, second=0, microsecond=0)
        if step >= 86400:
            first = first.replace(hour=0)
        ts = first.timestamp()
        while ts < start:
            ts += 3600 if step < 86400 else 86400
        if step == 4 * 3600:
            while datetime.fromtimestamp(ts).hour % 4:
                ts += 3600
        out = []
        while ts <= self.now:
            out.append((ts, fmt(datetime.fromtimestamp(ts))))
            ts += step
        return out

    # ── 마우스 올리면 그 시점 값 ─────────────────────────────
    def _hover(self, e) -> None:
        c, pal = self.canvas, self.pal
        c.delete("hover")
        if not self.series or self.table_mode:
            return
        left, top, right, bottom = self._geometry()
        if not left <= e.x <= right:
            return
        ts = self.now - self.hours * 3600 + (e.x - left) / (right - left) * self.hours * 3600
        c.create_line(e.x, top, e.x, bottom, fill=pal["axis"], tags=("hover", "crosshair"))
        c.tag_lower("crosshair")
        rows = []
        for key in self._ordered_keys():
            pts = self.series[key]
            times = [p[0] for p in pts]
            i = bisect.bisect_right(times, ts) - 1
            if i >= 0 and ts - pts[i][0] <= GAP_BREAK_S:
                rows.append((key, pts[i][1]))
                c.create_oval(self._x(pts[i][0]) - self.px(4), self._y(pts[i][1]) - self.px(4),
                              self._x(pts[i][0]) + self.px(4), self._y(pts[i][1]) + self.px(4),
                              fill=self._color(key), outline=pal["card"], width=self.px(2), tags="hover")
        if not rows:
            return
        d = datetime.fromtimestamp(ts)
        lines = [f"{d.month}/{d.day} {d:%H:%M}"] + [f"{self._name(k)}  {v:.0f}%" for k, v in rows]
        lh = self.px(17)
        tw = max(len(s) for s in lines) * self.px(7) + self.px(28)
        th = lh * len(lines) + self.px(10)
        x = e.x + self.px(14) if e.x + self.px(14) + tw < right else e.x - self.px(14) - tw
        y = min(max(e.y - th / 2, top), bottom - th)
        c.create_rectangle(x, y, x + tw, y + th, fill=pal["bg"], outline=pal["border"], tags="hover")
        for i, s in enumerate(lines):
            ty = y + self.px(5) + lh * i + lh / 2
            if i:
                key = rows[i - 1][0]
                c.create_rectangle(x + self.px(8), ty - self.px(4), x + self.px(16), ty + self.px(4),
                                   fill=self._color(key), outline="", tags="hover")
            c.create_text(x + self.px(22 if i else 8), ty, text=s, anchor="w", tags="hover",
                          font=(theme.FONT, 9, "bold") if not i else (theme.FONT, 9), fill=pal["text"])

    # ── 표로 보기 ─────────────────────────────────────────
    def _draw_table(self) -> None:
        if self.table:
            self.table.destroy()
        frame = tk.Frame(self.win, bg=self.pal["card"])
        frame.pack(fill="both", expand=True, padx=self.px(16), pady=(0, self.px(10)))
        self.table = frame
        tree = ttk.Treeview(frame, columns=("time", "name", "used"), show="headings")
        for col, text, width in (("time", t("g_col_time"), 140), ("name", t("g_col_item"), 200), ("used", t("g_col_used"), 100)):
            tree.heading(col, text=text)
            tree.column(col, width=self.px(width), anchor="w" if col != "used" else "e")
        rows = [(ts, self._name(key), used) for key, pts in self.series.items() for ts, used, _ in pts]
        for ts, name, used in sorted(rows, reverse=True)[:2000]:
            tree.insert("", "end", values=(datetime.fromtimestamp(ts).strftime("%m/%d %H:%M"), name, f"{used:.0f}%"))
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
