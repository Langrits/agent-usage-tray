"""팝업 옆에 붙는 작업 현황 패널: 지금 진행 중인 작업만 (질문·승인 대기, 한도로 멈춤, 작업 중, 기록 끊김).

패널은 테두리 없는 창으로 팝업 왼쪽에 붙는다. 사용 기록 패널이 열려 있으면 그 바깥쪽에 붙는다:
[작업 현황][사용 기록][팝업]. 왼쪽에 자리가 없으면 오른쪽에 붙는다.
"""
from __future__ import annotations

import logging
import os
import time
import tkinter as tk
from tkinter import font as tkfont

from . import format as fmt
from . import i18n, theme, winutil
from .activity import LIMITED, RUNNING, STALLED, WAITING, Item
from .history_panel import GAP, panel_x
from .i18n import t

WIDTH = 270
MAX_ROWS = 8
log = logging.getLogger("agent_usage")


def elide(text: str, width: int, measure) -> str:
    """폭(px)에 맞게 뒤를 '…'로 줄인다."""
    if measure(text) <= width:
        return text
    lo, hi = 0, len(text)
    while lo < hi:                 # '…'를 붙여도 들어가는 가장 긴 앞부분
        mid = (lo + hi + 1) // 2
        if measure(text[:mid].rstrip() + "…") <= width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + "…"


def duration(secs: float) -> str:
    mins = max(1, int(secs // 60))
    hours, mins = divmod(mins, 60)
    if not hours:
        return t("dur_m", m=mins)
    return t("dur_hm", h=hours, m=mins) if mins else t("dur_h", h=hours)


def limit_note(windows) -> str:
    """한도로 멈춘 작업 옆에 붙일 말: 다 쓴 한도가 풀리는 시각, 이미 풀렸으면 '한도 초기화됨'.

    windows는 그 서비스의 마지막 사용량 조회 결과 (없으면 None → 아무 말도 붙이지 않는다).
    """
    if not windows:
        return ""
    spent = [w for w in windows if w.used_pct is not None and w.used_pct >= 99.5]
    if not spent:
        return t("a_reset_done")
    resets = [w.resets_at for w in spent if w.resets_at]
    return t("a_resets", clock=fmt.clock(max(resets))) if len(resets) == len(spent) else ""   # 여럿이면 늦게 풀리는 것


def status_line(item: Item, now: float, windows=None) -> str:
    """둘째 줄: '앱 · 상태 · 시간'."""
    if item.status == STALLED:
        text = t("a_stalled", d=duration(now - (item.updated_at or now)))
    else:
        text = t(f"a_{item.status}")
        extra = limit_note(windows) if item.status == LIMITED else (duration(now - item.since) if item.since else "")
        if extra:
            text += " · " + extra
    return f"{item.label} · {text}"


class ActivityPanel:
    def __init__(self, popup):
        self.popup = popup
        self.app = popup.app
        self.pal = popup.app.pal
        self.px = popup.px
        self.win: tk.Toplevel | None = None
        self.visible = False
        self.body: tk.Frame | None = None
        self.rows: list[dict] = []
        self._sig = None

    # ── 보이기/숨기기·위치 ──────────────────────────────────
    def _create(self) -> None:
        pal, px = self.pal, self.px
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
        self.win.bind("<FocusOut>", lambda e: self.popup.win.after(150, self.popup._check_focus))
        head = tk.Frame(self.frame, bg=pal["bg"])
        head.pack(fill="x", padx=(px(14), px(12)), pady=(px(8), px(6)))    # 팝업 머리글과 같은 높이
        tk.Label(head, text=t("a_title"), font=(theme.FONT, 11, "bold"), fg=pal["text"],
                 bg=pal["bg"]).pack(side="left")
        self.count = tk.Label(head, text="", font=(theme.FONT, 9), fg=pal["muted"], bg=pal["bg"])
        self.count.pack(side="right", pady=(px(2), 0))
        self.title_font = tkfont.Font(family=theme.FONT, size=9)

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
        """팝업(사용 기록 패널이 열려 있으면 그것까지 합친 덩어리) 바깥쪽에 붙고, 위쪽을 팝업에 맞춘다."""
        if not (self.visible and self.popup.win):
            return
        pw = self.popup.win
        pw.update_idletasks()
        x0, y0, w0 = pw.winfo_x(), pw.winfo_y(), pw.winfo_width()
        x1 = x0 + w0
        hist = self.popup.history
        if hist.alive() and hist.placed:     # 막 옮긴 창의 위치는 아직 옛값일 수 있어 기록 패널이 정한 값을 쓴다
            hx, hw = hist.placed
            x0, x1 = min(x0, hx), max(x1, hx + hw)
        left, top, right, bottom = winutil.work_area_at(pw.winfo_x() + w0 // 2, y0)
        w = self.px(WIDTH)
        x = panel_x(x0, x1 - x0, w, self.px(GAP), left, right)
        self.frame.update_idletasks()
        h = min(self.frame.winfo_reqheight() + 2, bottom - y0)
        self.win.geometry(f"{w}x{h}+{x}+{y0}")

    # ── 내용 ─────────────────────────────────────────────
    def redraw(self) -> None:
        """목록 구성(항목·상태)이 같으면 글자만 고쳐서 깜빡이지 않게 한다."""
        if not self.win:
            return
        snap = self.app.activity
        items = snap.items if snap else []
        shown = items[:MAX_ROWS]
        sig = (i18n.lang(), snap is None, tuple((i.uid, i.status, bool(i.open_url)) for i in shown),
               len(items) - len(shown), tuple(snap.errors) if snap else ())
        if sig != self._sig:
            self._build(snap, shown, len(items) - len(shown))
            self._sig = sig
        now = time.time()
        width = self.px(WIDTH) - self.px(62)          # 패널 여백·상태 점을 뺀 제목 폭
        for refs, item in zip(self.rows, shown):
            refs["title"].configure(text=elide(item.title, width, self.title_font.measure))
            refs["sub"].configure(text=status_line(item, now, self._windows(item)))
        self.count.configure(text=t("a_count", n=len(items)) if items else "")
        self.follow()

    def _windows(self, item: Item):
        good = self.app.good.get(item.agent)
        return good.windows if good else None

    def _build(self, snap, shown: list[Item], more: int) -> None:
        pal, px = self.pal, self.px
        body = tk.Frame(self.frame, bg=pal["bg"])
        self.rows = []
        if snap is None:
            self._note(body, t("a_loading"))
        elif not shown:
            self._note(body, t("a_none"))
        for item in shown:
            self.rows.append(self._row(body, item))
        if more:
            self._note(body, t("a_more", n=more))
        if snap and snap.errors:
            self._note(body, t("a_error", names=", ".join(snap.errors)))
        if self.body:
            self.body.destroy()
        body.pack(fill="x", pady=(0, px(6)))
        self.body = body

    def _note(self, parent, text: str) -> None:
        tk.Label(parent, text=text, font=(theme.FONT, 8), fg=self.pal["muted"], bg=self.pal["bg"],
                 anchor="w", justify="left", wraplength=self.px(WIDTH - 30)) \
            .pack(fill="x", padx=self.px(14), pady=(0, self.px(4)))

    def _row(self, parent, item: Item) -> dict:
        pal, px = self.pal, self.px
        dot_color = {WAITING: theme.STATUS["warning"], LIMITED: theme.STATUS["critical"],
                     RUNNING: theme.STATUS["good"]}.get(item.status, pal["muted"])
        row = tk.Frame(parent, bg=pal["card"], highlightthickness=1, highlightbackground=pal["border"],
                       cursor="hand2" if item.open_url else "")
        row.pack(fill="x", padx=px(10), pady=(0, px(4)))
        dot = tk.Label(row, text="●", font=(theme.FONT, 8), fg=dot_color, bg=pal["card"])
        dot.grid(row=0, column=0, rowspan=2, sticky="n", padx=(px(8), px(6)), pady=(px(6), 0))
        title = tk.Label(row, text="", font=self.title_font, fg=pal["text"], bg=pal["card"], anchor="w")
        title.grid(row=0, column=1, sticky="w", pady=(px(5), 0), padx=(0, px(8)))
        sub = tk.Label(row, text="", font=(theme.FONT, 8), anchor="w", bg=pal["card"],
                       fg=pal["muted"] if item.status == STALLED else pal["text2"])
        sub.grid(row=1, column=1, sticky="w", pady=(0, px(6)), padx=(0, px(8)))
        parts = (row, dot, title, sub)
        if item.open_url:
            def paint(bg):
                for w in parts:
                    w.configure(bg=bg)

            def leave(e):          # 줄 안의 다른 글자로 옮겨 갈 때는 강조를 유지한다
                if row.winfo_containing(e.x_root, e.y_root) not in parts:
                    paint(pal["card"])
            for w in parts:
                w.bind("<Enter>", lambda e: paint(pal["hover"]))
                w.bind("<Leave>", leave)
                w.bind("<Button-1>", lambda e, url=item.open_url: self._open(url))
        return {"title": title, "sub": sub}

    def _open(self, url: str) -> None:
        try:
            os.startfile(url)
        except OSError:
            log.exception("open %s failed", url.split(":", 1)[0])
