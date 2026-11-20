"""설정 화면 (팝업과 같은 카드 모양). 바꾸는 즉시 저장하고 반영한다.

구역마다 카드 하나, 줄마다 왼쪽에 이름(과 설명), 오른쪽에 조작 부품을 둔다.
부품(스위치·선택 버튼·숫자 단추)은 tkinter 기본 부품 대신 직접 그린다. 스위치는 가장자리가
매끄럽게 보이도록 Pillow로 크게 그린 뒤 줄인 그림을 쓴다.
"""
from __future__ import annotations

import os
import tkinter as tk
import webbrowser
from tkinter import font as tkfont

from PIL import Image, ImageDraw, ImageTk

from . import APP_NAME, REPO_URL, __version__, theme, winutil
from .i18n import t
from .providers import ALL
from .store import DATA_DIR

MODES = ("auto", "show", "hide")
LANGS = (("auto", None), ("ko", "한국어"), ("en", "English"))     # None: 화면 언어로 '자동'
THRESHOLDS = (5, 10, 15, 20, 25, 30, 40, 50)                     # 부족 알림 기준(남은 양 %) 단계
REFRESH_MINUTES = (2, 3, 5, 10, 15, 20, 30, 60)
WIDTH = 440
# 설정 창에서만 쓰는 색: 바탕과 카드가 구분되고, 흐린 글자도 읽을 수 있게(대비 4.5:1 이상)
EXTRA = {
    "light": {"win": "#f4f4f1", "card": "#ffffff", "muted": "#6e6d68", "border": "#e4e3dc",
              "seg": "#efeee9", "seg_on": "#ffffff", "off": "#8a8984", "knob_on": "#ffffff"},
    "dark": {"win": "#0d0d0d", "card": "#1a1a19", "muted": "#9a9890", "border": "#2e2e2c",
             "seg": "#262624", "seg_on": "#3a3a37", "off": "#8a8984", "knob_on": "#0d0d0d"},
}


def step_value(values, current: int, direction: int) -> int:
    """정해진 단계에서 다음 값. 지금 값이 단계 사이(예: 17)면 그 방향의 가장 가까운 단계로."""
    if direction > 0:
        return next((v for v in values if v > current), values[-1])
    return next((v for v in reversed(values) if v < current), values[0])


def switch_image(on: bool, focused: bool, colors: dict, scale: float) -> Image.Image:
    """스위치 그림. 4배로 그려 줄여서 가장자리를 매끄럽게 한다. 둘레에는 포커스 테두리 자리를 둔다."""
    k = 4
    w, h, pad = 38 * scale * k, 22 * scale * k, 3 * scale * k
    size = (round(w + 2 * pad), round(h + 2 * pad))
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    line = max(1, round(1.5 * scale * k))
    if focused:
        d.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=size[1] / 2, outline=colors["text2"], width=line)
    box = (pad, pad, pad + w, pad + h)
    if on:
        d.rounded_rectangle(box, radius=h / 2, fill=colors["text"])
    else:
        d.rounded_rectangle(box, radius=h / 2, outline=colors["off"], width=line)
    knob = 13 * scale * k
    gap = (h - knob) / 2
    cx = (box[2] - gap - knob / 2) if on else (box[0] + gap + knob / 2)
    cy = pad + h / 2
    d.ellipse((cx - knob / 2, cy - knob / 2, cx + knob / 2, cy + knob / 2),
              fill=colors["knob_on"] if on else colors["off"])
    return img.resize((round(size[0] / k), round(size[1] / k)), Image.LANCZOS)


class Switch(tk.Label):
    """켜기/끄기 스위치. 클릭하거나, 탭 키로 옮겨 와 스페이스·엔터로 바꾼다."""

    def __init__(self, parent, colors: dict, scale: float, value: bool, on_change):
        super().__init__(parent, bg=parent["bg"], bd=0, cursor="hand2", takefocus=1)
        self.colors, self.scale, self.value, self.on_change = colors, scale, bool(value), on_change
        self.focused = False
        self.images: dict = {}
        for ev in ("<Button-1>", "<space>", "<Return>"):
            self.bind(ev, self._flip)
        self.bind("<FocusIn>", lambda e: self._set_focus(True))
        self.bind("<FocusOut>", lambda e: self._set_focus(False))
        self._draw()

    def _draw(self) -> None:
        key = (self.value, self.focused)
        if key not in self.images:
            self.images[key] = ImageTk.PhotoImage(switch_image(*key, self.colors, self.scale), master=self)
        self.configure(image=self.images[key])

    def _set_focus(self, on: bool) -> None:
        self.focused = on
        self._draw()

    def _flip(self, _e=None):
        self.value = not self.value
        self._draw()
        self.on_change(self.value)
        return "break"


class Segmented(tk.Frame):
    """한 번에 하나를 고르는 선택 버튼 묶음 (예: 자동 | 표시 | 숨김)."""

    def __init__(self, parent, colors: dict, px, options: list[tuple[str, str]], current: str, on_change):
        super().__init__(parent, bg=colors["seg"], padx=px(2), pady=px(2))
        self.colors, self.current, self.on_change = colors, current, on_change
        self.items: dict[str, tk.Label] = {}
        bold = tkfont.Font(family=theme.FONT, size=9, weight="bold")
        for i, (key, text) in enumerate(options):
            # 고른 항목만 굵게 해도 옆 버튼이 밀리지 않도록, 굵은 글자 폭으로 칸을 고정한다
            cell = tk.Frame(self, width=bold.measure(text) + px(20), height=bold.metrics("linespace") + px(6))
            cell.pack_propagate(False)
            cell.pack(side="left", padx=(px(2) if i else 0, 0))
            lbl = tk.Label(cell, text=text, cursor="hand2", takefocus=1, highlightthickness=1, bd=0)
            lbl.pack(fill="both", expand=True)
            for ev in ("<Button-1>", "<space>", "<Return>"):
                lbl.bind(ev, lambda e, k=key: self.pick(k))
            lbl.bind("<Enter>", lambda e, k=key: self._hover(k, True))
            lbl.bind("<Leave>", lambda e, k=key: self._hover(k, False))
            self.items[key] = lbl
        self._style()

    def pick(self, key: str):
        if key != self.current:
            self.current = key
            self._style()
            self.on_change(key)
        return "break"

    def _hover(self, key: str, on: bool) -> None:
        if key != self.current:
            self.items[key].configure(fg=self.colors["text"] if on else self.colors["text2"])

    def _style(self) -> None:
        c = self.colors
        for key, lbl in self.items.items():
            on = key == self.current
            bg = c["seg_on"] if on else c["seg"]
            lbl.configure(bg=bg, fg=c["text"] if on else c["text2"], highlightbackground=bg, highlightcolor=c["text2"],
                          font=(theme.FONT, 9, "bold") if on else (theme.FONT, 9))
            lbl.master.configure(bg=bg)


class Stepper(tk.Frame):
    """− 값 + 단추. 정해진 단계(values) 안에서만 움직인다."""

    def __init__(self, parent, colors: dict, px, values, current: int, fmt, on_change):
        super().__init__(parent, bg=parent["bg"], highlightthickness=1, highlightbackground=colors["border"])
        self.colors, self.values, self.current, self.fmt, self.on_change = colors, values, current, fmt, on_change
        self.enabled = True
        bold = tkfont.Font(family=theme.FONT, size=10, weight="bold")
        width = max(bold.measure(fmt(v)) for v in (*values, current)) + px(12)
        self.minus = self._button("−", -1, px)
        box = tk.Frame(self, bg=parent["bg"], width=width, height=bold.metrics("linespace") + px(8))
        box.pack_propagate(False)
        box.pack(side="left")
        self.label = tk.Label(box, text=fmt(current), font=(theme.FONT, 10, "bold"), bg=parent["bg"], fg=colors["text"])
        self.label.pack(fill="both", expand=True)
        self.plus = self._button("+", 1, px)

    def _button(self, text: str, direction: int, px) -> tk.Label:
        c = self.colors
        b = tk.Label(self, text=text, font=(theme.FONT, 11), fg=c["text2"], bg=self["bg"], padx=px(9), cursor="hand2",
                     takefocus=1, highlightthickness=1, highlightbackground=self["bg"], highlightcolor=c["text2"])
        b.pack(side="left", fill="y")
        for ev in ("<Button-1>", "<space>", "<Return>"):
            b.bind(ev, lambda e: self.step(direction))
        b.bind("<Enter>", lambda e: self.enabled and b.configure(bg=c["hover"]))
        b.bind("<Leave>", lambda e: b.configure(bg=self["bg"]))
        return b

    def step(self, direction: int):
        if self.enabled:
            new = step_value(self.values, self.current, direction)
            if new != self.current:
                self.current = new
                self.label.configure(text=self.fmt(new))
                self.on_change(new)
        return "break"

    def set_enabled(self, on: bool) -> None:
        """꺼진 알림의 기준처럼 지금 쓰이지 않는 값은 흐리게 하고 바꾸지 못하게 한다."""
        self.enabled = on
        c = self.colors
        self.label.configure(fg=c["text"] if on else c["muted"])
        for b in (self.minus, self.plus):
            b.configure(fg=c["text2"] if on else c["muted"], cursor="hand2" if on else "")


class SettingsWindow:
    def __init__(self, app):
        self.app = app
        self.dark = app.pal is theme.DARK
        self.c = {**app.pal, **EXTRA["dark" if self.dark else "light"]}
        self.px = lambda v: int(round(v * app.scale))
        self.win = tk.Toplevel(app.root)
        self.win.configure(bg=self.c["win"])
        self.win.resizable(False, False)
        self.win.bind_all("<MouseWheel>", self._on_wheel, add="+")
        self.status: dict[str, tuple[tk.Label, tk.Label]] = {}
        self.scrolling = False
        self.placed = False
        self.build()
        winutil.dark_title_bar(self.win, self.dark)
        winutil.bring_to_front(self.win)

    def alive(self) -> bool:
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def focus(self) -> None:
        winutil.bring_to_front(self.win)

    # ── 공통 ─────────────────────────────────────────────
    def _text(self, parent, text, size=9, color="text", bold=False, **kw) -> tk.Label:
        kw.setdefault("bg", parent["bg"])
        return tk.Label(parent, text=text, fg=self.c[color], font=(theme.FONT, size, "bold") if bold else (theme.FONT, size),
                        **kw)

    def _section(self, title: str, first: bool = False) -> tk.Frame:
        px = self.px
        self._text(self.body, title, 9, "text2", bold=True).pack(anchor="w", padx=px(4),
                                                                 pady=(0 if first else px(14), px(5)))
        card = tk.Frame(self.body, bg=self.c["card"], highlightthickness=1, highlightbackground=self.c["border"])
        card.pack(fill="x")
        return card

    def _row(self, card: tk.Frame, index: int) -> tk.Frame:
        if index:
            tk.Frame(card, bg=self.c["border"], height=1).pack(fill="x")
        row = tk.Frame(card, bg=self.c["card"], padx=self.px(12), pady=self.px(8))
        row.pack(fill="x")
        return row

    def _labels(self, row: tk.Frame, title: str, help_text: str | None = None) -> list[tk.Label]:
        box = tk.Frame(row, bg=self.c["card"])
        box.pack(side="left", fill="x", expand=True)
        out = [self._text(box, title, 10)]
        out[0].pack(anchor="w")
        if help_text:
            out.append(self._text(box, help_text, 9, "text2", wraplength=self.px(300), justify="left"))
            out[1].pack(anchor="w", pady=(self.px(1), 0))
        return out

    def _link(self, parent, text: str, command) -> tk.Label:
        lbl = tk.Label(parent, text=text, fg=self.c["text2"], bg=parent["bg"], cursor="hand2", takefocus=1,
                       font=(theme.FONT, 9, "underline"), highlightthickness=1, highlightbackground=parent["bg"],
                       highlightcolor=self.c["text2"])
        for ev in ("<Button-1>", "<Return>"):
            lbl.bind(ev, lambda e: command())
        return lbl

    # ── 화면 ─────────────────────────────────────────────
    def build(self) -> None:
        for w in self.win.winfo_children():
            w.destroy()
        app, c, px, s = self.app, self.c, self.px, self.app.settings
        self.win.title(f"{t('s_title')} · {APP_NAME}")
        # 화면보다 길면(배율이 큰 노트북 등) 창 안에서 스크롤한다
        self.canvas = tk.Canvas(self.win, bg=c["win"], highlightthickness=0, bd=0, width=px(WIDTH))
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = tk.Frame(self.canvas, bg=c["win"], padx=px(16), pady=px(14))
        self.canvas.create_window(0, 0, window=self.body, anchor="nw", width=px(WIDTH))

        # 서비스
        card = self._section(t("s_services"), first=True)
        modes = [(m, t(f"s_mode_{m}")) for m in MODES]
        self.status = {}
        for i, mod in enumerate(ALL):
            row = self._row(card, i)
            Segmented(row, c, px, modes, app.service_mode(mod.ID),
                      lambda mode, pid=mod.ID: self._set_mode(pid, mode)).pack(side="right")
            left = tk.Frame(row, bg=c["card"])
            left.pack(side="left", fill="x", expand=True)
            self._text(left, mod.TITLE, 10, bold=True).pack(side="left")
            if mod.EXPERIMENTAL:
                self._text(left, t("experimental"), 8, "text2", padx=px(4), highlightthickness=1,
                           highlightbackground=c["border"]).pack(side="left", padx=(px(6), 0))
            dot = self._text(left, "", 8)
            dot.pack(side="left", padx=(px(8), px(3)))
            state = self._text(left, "", 9, "text2")
            state.pack(side="left")
            self.status[mod.ID] = (dot, state)
        self._text(self.body, t("s_mode_help"), 9, "muted", wraplength=px(WIDTH - 40), justify="left") \
            .pack(anchor="w", padx=px(4), pady=(px(6), 0))
        self.refresh_status()

        # 알림
        card = self._section(t("s_alerts"))
        row = self._row(card, 0)
        Switch(row, c, app.scale, s["notify_low"], self._set_low).pack(side="right")
        self._labels(row, t("s_low"), t("s_low_help"))
        row = self._row(card, 1)
        self.threshold = Stepper(row, c, px, THRESHOLDS, int(s["low_threshold"]), lambda v: f"{v}%",
                                 lambda v: app.set_setting("low_threshold", v))
        self.threshold.pack(side="right")
        self.threshold_labels = self._labels(row, t("s_threshold"), t("s_threshold_help"))
        self._dim_threshold()
        row = self._row(card, 2)
        Switch(row, c, app.scale, s["notify_reset"], lambda v: app.set_setting("notify_reset", v)).pack(side="right")
        self._labels(row, t("s_reset"), t("s_reset_help"))

        # 일반
        card = self._section(t("s_general"))
        row = self._row(card, 0)
        Stepper(row, c, px, REFRESH_MINUTES, int(s["refresh_minutes"]), lambda v: t("s_minutes", n=v),
                lambda v: app.set_setting("refresh_minutes", v)).pack(side="right")
        self._labels(row, t("s_refresh"))
        row = self._row(card, 1)
        Switch(row, c, app.scale, s["auto_refresh_login"],
               lambda v: app.set_setting("auto_refresh_login", v)).pack(side="right")
        self._labels(row, t("s_auto_refresh"), t("s_auto_refresh_help"))
        row = self._row(card, 2)
        Switch(row, c, app.scale, winutil.autostart_enabled(), winutil.set_autostart).pack(side="right")
        self._labels(row, t("s_autostart"))
        row = self._row(card, 3)
        langs = [(k, name or t("s_lang_auto")) for k, name in LANGS]
        Segmented(row, c, px, langs, s["language"] or "auto", self._set_language).pack(side="right")
        self._labels(row, t("s_language"))

        # 아래쪽
        foot = tk.Frame(self.body, bg=c["win"])
        foot.pack(fill="x", pady=(px(18), 0))
        self._text(foot, f"{APP_NAME} {__version__}", 9, "muted").pack(side="left", padx=(px(4), 0))
        self._link(foot, t("s_data"), lambda: os.startfile(DATA_DIR)).pack(side="right")
        if REPO_URL:
            self._link(foot, "GitHub", lambda: webbrowser.open(REPO_URL)).pack(side="right", padx=(0, px(14)))
        self._fit()

    def _fit(self) -> None:
        """내용 높이에 맞추되, 작업 영역보다 길면 그만큼만 열고 스크롤한다."""
        self.body.update_idletasks()
        need = self.body.winfo_reqheight()
        left, top, right, bottom = winutil.work_area()
        room = bottom - top - self.px(80)          # 제목 줄·여백
        self.scrolling = need > room
        self.canvas.configure(height=min(need, room), scrollregion=(0, 0, self.px(WIDTH), need))
        if self.scrolling:
            bar = tk.Scrollbar(self.win, orient="vertical", command=self.canvas.yview)
            self.canvas.configure(yscrollcommand=bar.set)
            bar.pack(side="right", fill="y")
        if not self.placed:
            # 처음 열 때만 자리를 잡는다: 항상 위에 떠 있는 팝업·패널 왼쪽(자리가 없으면 작업 영역 가운데),
            # 세로는 작업 영역 가운데라 작업 표시줄에 가리지 않는다
            self.placed = True
            self.win.update_idletasks()
            w, h = self.win.winfo_reqwidth(), self.win.winfo_reqheight()
            block = self.app.popup.block_left()
            if block is not None and block - self.px(12) - w >= left:
                x = block - self.px(12) - w
            else:
                x = left + max(0, (right - left - w) // 2)
            y = top + max(0, (bottom - top - h - self.px(32)) // 2)    # 32: 제목 줄
            self.win.geometry(f"+{x}+{y}")

    def _on_wheel(self, e) -> None:
        if not (self.scrolling and self.alive()):
            return
        try:
            inside = e.widget.winfo_toplevel() == self.win
        except (AttributeError, tk.TclError):
            inside = False
        if inside:
            self.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")

    def refresh_status(self) -> None:
        for mod in ALL:
            refs = self.status.get(mod.ID)
            if refs is None:
                continue
            ok = mod.detect()
            dot, label = refs
            dot.configure(text="●" if ok else "○", fg=theme.STATUS["good"] if ok else self.c["muted"])
            label.configure(text=t("s_connected") if ok else t("s_not_connected"),
                            fg=self.c["text2"] if ok else self.c["muted"])

    def _dim_threshold(self) -> None:
        on = bool(self.app.settings["notify_low"])
        self.threshold.set_enabled(on)
        for lbl, color in zip(self.threshold_labels, ("text", "text2")):
            lbl.configure(fg=self.c[color] if on else self.c["muted"])

    def _set_low(self, on: bool) -> None:
        self.app.set_setting("notify_low", on)
        self._dim_threshold()

    def _set_mode(self, pid: str, mode: str) -> None:
        services = dict(self.app.settings["services"] or {})
        services[pid] = mode
        self.app.set_setting("services", services)

    def _set_language(self, choice: str) -> None:
        self.app.set_setting("language", choice)
        self.win.after_idle(self.build)        # 누른 버튼이 들어 있는 화면을 그 처리 도중에 지우지 않게
