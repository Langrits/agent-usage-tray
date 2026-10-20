"""트레이 아이콘 + 팝업 창. 팝업을 고정하면 위젯처럼 화면에 남는다.

tkinter는 메인 스레드, pystray(트레이)는 별도 스레드에서 돈다.
트레이 메뉴·조회 결과는 큐로 메인 스레드에 넘겨서 처리한다.
"""
from __future__ import annotations

import logging
import os
import queue
import sys
import threading
import time
import tkinter as tk

import pystray
from PIL import Image, ImageDraw, ImageFont

from . import APP_NAME, __version__, i18n, theme, winutil
from . import format as fmt
from .graph import HistoryWindow
from .i18n import t
from .models import Usage
from .notify import Notifier
from .providers import ALL, BY_ID, Options, fetch_all
from .settings_window import SettingsWindow
from .store import DATA_DIR, History, settings

GLYPH = {"refresh": "", "chart": "", "pin": "", "unpin": "",
         "close": "", "settings": ""}
log = logging.getLogger("agent_usage")


def tray_image(text: str, color: str) -> Image.Image:
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=14, fill=color)
    ink = "#0b0b0b" if color == theme.STATUS["warning"] else "#ffffff"
    try:
        font = ImageFont.truetype("segoeuib.ttf", 44 if len(text) <= 2 else 31)
    except OSError:
        font = ImageFont.load_default()
    d.text((size / 2, size / 2 + 1), text, fill=ink, font=font, anchor="mm")
    return img


class App:
    def __init__(self):
        winutil.enable_dpi_awareness()
        self.settings = settings()
        i18n.set_language(self.settings["language"])
        self.history = History()
        self.history.prune()
        self._notes: list[tuple[str, str]] = []
        self.notifier = Notifier(self.settings, lambda title, msg: self._notes.append((title, msg)))
        self.latest: dict[str, Usage] = {}    # 마지막 조회 결과(오류 포함)
        self.good: dict[str, Usage] = {}      # 마지막 성공 결과
        self.visible: list[str] = self.compute_visible()
        self.updated_at: float | None = None
        self.fetching = False
        self._next_job = None
        self._last_tick = time.time()
        self._tray_state = None
        self.q: queue.Queue = queue.Queue()

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.report_callback_exception = lambda *exc: log.error("tk callback", exc_info=exc)
        self.scale = self.root.winfo_fpixels("1i") / 96
        self.pal = theme.palette()
        self.popup = Popup(self)
        self.graph: HistoryWindow | None = None
        self.settings_win: SettingsWindow | None = None
        self.icon = pystray.Icon("AgentUsage", tray_image("…", theme.STATUS["none"]), APP_NAME,
                                 menu=self._menu())

    # ── 서비스 표시 여부 ─────────────────────────────────────
    def service_mode(self, pid: str) -> str:
        return (self.settings["services"] or {}).get(pid, "auto")

    def compute_visible(self) -> list[str]:
        out = []
        for mod in ALL:
            mode = self.service_mode(mod.ID)
            if mode == "show" or (mode == "auto" and mod.detect()):
                out.append(mod.ID)
        return out

    def options(self) -> Options:
        return Options(auto_refresh_login=bool(self.settings["auto_refresh_login"]))

    def set_setting(self, key: str, value) -> None:
        """설정 화면에서 바꾼 값을 저장하고 바로 반영한다."""
        self.settings[key] = value
        if key == "services":
            self.visible = self.compute_visible()
            self.popup.render_if_visible()
            self._update_tray()
            self.refresh()
        elif key == "language":
            i18n.set_language(value)
            self.icon.update_menu()
            if self.graph and self.graph.alive():
                self.graph.win.destroy()
                self.graph = HistoryWindow(self)
            self.refresh()          # 한도 이름 등은 조회할 때 만들어지므로 새로 조회
        elif key == "refresh_minutes":
            if not self.fetching:
                self.schedule_refresh(value * 60)
            self.popup.render_if_visible()
        elif key == "low_threshold":
            self._update_tray()
            self.popup.render_if_visible()
        elif key == "auto_refresh_login":
            self.refresh()

    # ── 트레이 메뉴 ─────────────────────────────────────────
    def _menu(self) -> pystray.Menu:
        item = pystray.MenuItem
        return pystray.Menu(
            item(lambda _: t("menu_toggle"), lambda: self.post("toggle"), default=True),
            item(lambda _: t("menu_refresh"), lambda: self.post("refresh")),
            item(lambda _: t("menu_pin"), lambda: self.post("pin"), checked=lambda _: bool(self.settings["pinned"])),
            item(lambda _: t("menu_history"), lambda: self.post("graph")),
            item(lambda _: t("menu_settings"), lambda: self.post("settings")),
            pystray.Menu.SEPARATOR,
            item(f"{APP_NAME} v{__version__}", lambda: None, enabled=False),
            item(lambda _: t("menu_quit"), lambda: self.post("quit")),
        )

    def post(self, *msg) -> None:
        self.q.put(msg)

    def _poll(self) -> None:
        try:
            while True:
                self._handle(*self.q.get_nowait())
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _handle(self, kind: str, arg=None) -> None:
        if kind == "toggle":
            self.popup.toggle()
        elif kind == "refresh":
            self.refresh()
        elif kind == "results":
            self._on_results(arg)
        elif kind == "pin":
            self.popup.set_pinned(not self.settings["pinned"])
        elif kind == "graph":
            self.open_graph()
        elif kind == "settings":
            self.open_settings()
        elif kind == "quit":
            self.icon.stop()
            self.root.destroy()

    # ── 조회 ─────────────────────────────────────────────
    def refresh(self) -> None:
        if self.fetching:
            return
        if self._next_job:
            self.root.after_cancel(self._next_job)
            self._next_job = None
        self.visible = self.compute_visible()
        self.fetching = True
        self.popup.render_if_visible()
        ids, opts = list(self.visible), self.options()
        threading.Thread(target=lambda: self.post("results", fetch_all(ids, opts)), daemon=True).start()

    def schedule_refresh(self, delay_s: float) -> None:
        if self._next_job:
            self.root.after_cancel(self._next_job)
        self._next_job = self.root.after(int(delay_s * 1000), self.refresh)

    def _on_results(self, results: dict[str, Usage]) -> None:
        self.fetching = False
        self.updated_at = time.time()
        for pid, u in results.items():
            self.latest[pid] = u
            if u.ok:
                self.good[pid] = u
                self.history.add(u)
                self.notifier.check(u)
            else:
                log.info("%s: %s", pid, u.error)
        self._flush_notes()
        self._update_tray()
        self.popup.render_if_visible()
        if self.graph and self.graph.alive():
            self.graph.redraw()
        if self.settings_win and self.settings_win.alive():
            self.settings_win.refresh_status()
        self.schedule_refresh(self.settings["refresh_minutes"] * 60)

    def _tick(self) -> None:
        """초기화 시각이 지나면 30초 뒤 다시 조회하고, 남은 시간 표시를 갱신한다."""
        now = time.time()
        passed = any(w.resets_at and self._last_tick < w.resets_at <= now
                     for pid in self.visible if pid in self.good for w in self.good[pid].windows)
        self._last_tick = now
        if passed and not self.fetching:
            self.schedule_refresh(30)
        self._update_tray()
        self.popup.render_if_visible()
        self.root.after(20000, self._tick)

    def _flush_notes(self) -> None:
        notes, self._notes = self._notes, []
        if not notes:
            return
        if len(notes) == 1:
            title, msg = notes[0]
        else:
            title, msg = t("n_many", n=len(notes)), "\n".join(m for _, m in notes)
        try:
            self.icon.notify(msg[:250], title[:60])
        except Exception:
            log.exception("notify failed")

    def _update_tray(self) -> None:
        parts, worst = [], None
        for pid in self.visible:
            u = self.good.get(pid)
            w = u.most_urgent() if u else None
            if w:
                parts.append(f"{u.title} {w.remaining_pct:.0f}%")
                worst = w.remaining_pct if worst is None else min(worst, w.remaining_pct)
            elif pid in self.latest:
                parts.append(t("tooltip_attention", title=self.latest[pid].title))
        text = "?" if worst is None else f"{worst:.0f}"
        color = theme.STATUS[theme.level(worst, self.settings["low_threshold"])]
        title = t("tooltip", parts=" · ".join(parts))[:127] if parts else APP_NAME
        if self._tray_state != (text, color, title):     # 바뀔 때만 다시 그린다
            self._tray_state = (text, color, title)
            self.icon.icon = tray_image(text, color)
            self.icon.title = title

    def open_graph(self) -> None:
        if self.graph and self.graph.alive():
            self.graph.focus()
        else:
            self.graph = HistoryWindow(self)

    def open_settings(self) -> None:
        if self.settings_win and self.settings_win.alive():
            self.settings_win.focus()
        else:
            self.settings_win = SettingsWindow(self)

    # ── 실행 ─────────────────────────────────────────────
    def run(self) -> None:
        threading.Thread(target=self.icon.run, daemon=True).start()
        self.root.after(100, self._poll)
        self.root.after(300, self.refresh)
        self.root.after(20000, self._tick)
        if self.settings["pinned"]:
            self.root.after(400, self.popup.show)
        self.root.mainloop()


class Popup:
    """창 구조(카드·줄 구성)가 같으면 글자·막대만 고쳐서 깜빡이지 않게 한다."""
    WIDTH = 340

    def __init__(self, app: App):
        self.app = app
        self.win: tk.Toplevel | None = None
        self.body: tk.Frame | None = None
        self.visible = False
        self.hidden_at = 0.0
        self.refs: dict = {}
        self._sig = None
        self._geom = None
        self._drag = (0, 0)

    def px(self, v: float) -> int:
        return int(round(v * self.app.scale))

    @property
    def pinned(self) -> bool:
        return bool(self.app.settings["pinned"])

    # ── 보이기/숨기기 ─────────────────────────────────────
    def _create(self) -> None:
        pal = self.app.pal
        self.win = tk.Toplevel(self.app.root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=pal["border"])
        self.win.bind("<Escape>", lambda e: self.hide())
        self.win.bind("<FocusOut>", lambda e: self.win.after(150, self._check_focus))
        self.frame = tk.Frame(self.win, bg=pal["bg"])
        self.frame.pack(fill="both", expand=True, padx=1, pady=1)

    def show(self) -> None:
        if not self.win:
            self._create()
        self.visible = True
        self.render()
        self.win.deiconify()
        self.win.lift()
        winutil.round_corners(self.win)
        if not self.pinned:
            self.win.after(50, self.win.focus_force)

    def hide(self) -> None:
        if self.win and self.visible:
            self.win.withdraw()
            self.visible = False
            self.hidden_at = time.time()

    def toggle(self) -> None:
        if self.visible:
            self.hide()
        elif time.time() - self.hidden_at > 0.4:   # 트레이 클릭으로 포커스를 잃어 막 닫힌 경우는 다시 열지 않음
            self.show()

    def _check_focus(self) -> None:
        if self.pinned or not self.visible:
            return
        try:
            focused = self.win.focus_get()
        except KeyError:
            focused = None
        if focused is None:
            self.hide()

    def set_pinned(self, on: bool) -> None:
        if on and self.win and self.visible:
            self.app.settings["pin_pos"] = [self.win.winfo_x(), self.win.winfo_y()]
        self.app.settings["pinned"] = on
        self.app.icon.update_menu()
        self.show()

    def _place(self) -> None:
        self.win.update_idletasks()
        w, h = self.px(self.WIDTH), self.win.winfo_reqheight()
        left, top, right, bottom = winutil.work_area()
        pos = self.app.settings["pin_pos"]
        if self.pinned and pos:
            x = min(max(pos[0], left), right - w)
            y = min(max(pos[1], top), bottom - h)
        else:
            x, y = right - w - self.px(12), bottom - h - self.px(12)
        if (w, h, x, y) != self._geom:       # 크기·위치가 바뀔 때만 옮긴다
            self._geom = (w, h, x, y)
            self.win.geometry(f"{w}x{h}+{x}+{y}")

    # 고정 상태에서는 머리글을 끌어 옮길 수 있다
    def _drag_start(self, e) -> None:
        self._drag = (e.x_root - self.win.winfo_x(), e.y_root - self.win.winfo_y())

    def _drag_move(self, e) -> None:
        if self.pinned:
            self.win.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    def _drag_end(self, e) -> None:
        if self.pinned:
            self.app.settings["pin_pos"] = [self.win.winfo_x(), self.win.winfo_y()]
            self._geom = None

    # ── 보여줄 내용 ───────────────────────────────────────
    def _model(self) -> dict:
        app = self.app
        threshold = app.settings["low_threshold"]
        cards = []
        for pid in app.visible:
            mod = BY_ID[pid]
            latest, good = app.latest.get(pid), app.good.get(pid)
            shown = good or latest
            card = {"id": pid, "title": mod.TITLE, "tag": t("experimental") if mod.EXPERIMENTAL else "",
                    "plan": (shown.plan or "") if shown else "",
                    "meta": f"{good.source} · {fmt.ago(good.data_at)}" if good else "",
                    "loading": latest is None, "rows": [], "reset": None, "extras": [],
                    "problem": None, "hint": ""}
            if good:
                for w in good.windows:
                    r = w.remaining_pct
                    cd = fmt.countdown(w.resets_at)
                    when = t("reset_done") if cd == t("cd_done") else (
                        t("resets_in", cd=cd, clock=fmt.clock(w.resets_at)) if cd else "")
                    card["rows"].append({"key": w.key, "label": w.label, "note": w.note,
                                         "pct": t("pct_left", pct=f"{r:.0f}"), "remaining": r,
                                         "color": theme.STATUS[theme.level(r, threshold)], "when": when})
                rc = good.reset_credits
                if rc is not None:
                    card["reset"] = ((t("rc_count", n=rc.count)
                                      + (t("rc_expires", clock=fmt.clock(rc.expires_at)) if rc.expires_at else ""), True)
                                     if rc.count else (t("rc_none"), False))
                card["extras"] = list(good.extra)
            if latest and latest.error:
                card["problem"] = latest.error + (t("last_success", ago=fmt.ago(good.data_at)) if good else "")
                card["hint"] = latest.hint or ""
            cards.append(card)
        if app.fetching:
            footer = t("refreshing")
        elif app.updated_at:
            footer = t("updated", ago=fmt.ago(app.updated_at), n=app.settings["refresh_minutes"])
        else:
            footer = ""
        return {"cards": cards, "footer": footer}

    def _signature(self, m: dict):
        return (self.pinned, i18n.lang(), tuple(
            (c["id"], c["loading"], bool(c["plan"]), bool(c["meta"]),
             tuple((r["key"], bool(r["note"]), bool(r["when"])) for r in c["rows"]),
             c["reset"][1] if c["reset"] else None, len(c["extras"]), c["problem"] is not None, bool(c["hint"]))
            for c in m["cards"]))

    def render_if_visible(self) -> None:
        if self.visible:
            self.render()

    def render(self) -> None:
        m = self._model()
        sig = self._signature(m)
        if self.body is None or sig != self._sig:
            self._build(m)
            self._sig = sig
        else:
            self._apply(m)
        self._place()

    # ── 처음 만들기 ───────────────────────────────────────
    def _label(self, parent, text, size=9, color="text", bold=False, bg="card", **kw) -> tk.Label:
        pal = self.app.pal
        return tk.Label(parent, text=text, font=(theme.FONT, size, "bold") if bold else (theme.FONT, size),
                        fg=pal[color], bg=pal[bg], **kw)

    def _icon_button(self, parent, glyph: str, command, active=False) -> tk.Label:
        pal = self.app.pal
        b = tk.Label(parent, text=glyph, font=(theme.ICON_FONT, 10), cursor="hand2",
                     fg=pal["text"] if active else pal["text2"], bg=pal["bg"],
                     padx=self.px(6), pady=self.px(4))
        b.bind("<Enter>", lambda e: b.configure(bg=pal["hover"]))
        b.bind("<Leave>", lambda e: b.configure(bg=pal["bg"]))
        b.bind("<Button-1>", lambda e: self.win.after_idle(command))
        return b

    def _build(self, m: dict) -> None:
        pal = self.app.pal
        body = tk.Frame(self.frame, bg=pal["bg"])
        self.refs = {"cards": {}}
        self._header(body)
        if not m["cards"]:
            self._label(body, t("no_services"), 9, "text2", bg="bg", justify="left") \
                .pack(anchor="w", padx=self.px(14), pady=self.px(10))
        for c in m["cards"]:
            self.refs["cards"][c["id"]] = self._build_card(body, c)
        self.refs["footer"] = self._label(body, m["footer"], 8, "muted", bg="bg")
        self.refs["footer"].pack(anchor="e", padx=self.px(14), pady=(self.px(2), self.px(8)))
        if self.body:
            self.body.destroy()
        body.pack(fill="both", expand=True)
        self.body = body

    def _header(self, parent) -> None:
        px = self.px
        head = tk.Frame(parent, bg=self.app.pal["bg"], cursor="fleur" if self.pinned else "")
        head.pack(fill="x", padx=(px(14), px(8)), pady=(px(8), px(2)))
        title = self._label(head, t("app_title"), 11, bold=True, bg="bg")
        title.pack(side="left")
        for glyph, cmd, active in ((GLYPH["close"], self.hide, False),
                                   (GLYPH["unpin" if self.pinned else "pin"],
                                    lambda: self.set_pinned(not self.pinned), self.pinned),
                                   (GLYPH["settings"], self.app.open_settings, False),
                                   (GLYPH["chart"], self.app.open_graph, False),
                                   (GLYPH["refresh"], self.app.refresh, False)):
            self._icon_button(head, glyph, cmd, active).pack(side="right")
        for w in (head, title):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<ButtonRelease-1>", self._drag_end)

    def _build_card(self, parent, c: dict) -> dict:
        pal, px = self.app.pal, self.px
        refs: dict = {"rows": {}, "extras": []}
        card = tk.Frame(parent, bg=pal["card"], highlightthickness=1, highlightbackground=pal["border"])
        card.pack(fill="x", padx=px(10), pady=px(4))
        inner = tk.Frame(card, bg=pal["card"])
        inner.pack(fill="x", padx=px(12), pady=(px(9), px(11)))

        top = tk.Frame(inner, bg=pal["card"])
        top.pack(fill="x")
        self._label(top, c["title"], 11, bold=True).pack(side="left")
        if c["tag"]:
            self._label(top, c["tag"], 8, "muted").pack(side="left", padx=(px(4), 0), pady=(px(2), 0))
        if c["plan"]:
            refs["plan"] = self._label(top, c["plan"], 9, "text2")
            refs["plan"].pack(side="left", padx=(px(6), 0), pady=(px(2), 0))
        if c["meta"]:
            refs["meta"] = self._label(top, c["meta"], 8, "muted")
            refs["meta"].pack(side="right")

        if c["loading"]:
            self._label(inner, t("loading"), 9, "muted").pack(anchor="w", pady=(px(6), 0))
            return refs
        for row in c["rows"]:
            refs["rows"][row["key"]] = self._build_row(inner, row)
        if c["reset"]:
            text, bold = c["reset"]
            refs["reset"] = self._label(inner, text, 9, "text" if bold else "muted", bold=bold)
            refs["reset"].pack(anchor="w", pady=(px(8), 0))
        for line in c["extras"]:
            lbl = self._label(inner, line, 8, "text2", wraplength=px(self.WIDTH - 60), justify="left")
            lbl.pack(anchor="w", pady=(px(3), 0))
            refs["extras"].append(lbl)
        if c["problem"] is not None:
            row = tk.Frame(inner, bg=pal["card"])
            row.pack(fill="x", pady=(px(8), 0))
            tk.Label(row, text="●", font=(theme.FONT, 7), fg=theme.STATUS["warning"], bg=pal["card"]) \
                .pack(side="left", anchor="n", pady=(px(2), 0))
            refs["problem"] = self._label(row, c["problem"], 9, wraplength=px(self.WIDTH - 70), justify="left")
            refs["problem"].pack(side="left", padx=(px(4), 0))
            if c["hint"]:
                refs["hint"] = self._label(inner, c["hint"], 8, "text2", wraplength=px(self.WIDTH - 60),
                                           justify="left")
                refs["hint"].pack(anchor="w", padx=(px(14), 0), pady=(px(2), 0))
        return refs

    def _build_row(self, parent, row: dict) -> dict:
        pal, px = self.app.pal, self.px
        refs = {}
        line = tk.Frame(parent, bg=pal["card"])
        line.pack(fill="x", pady=(px(8), 0))
        refs["label"] = self._label(line, row["label"], 9, "text2")
        refs["label"].pack(side="left")
        if row["note"]:
            refs["note"] = self._label(line, row["note"], 8, "muted")
            refs["note"].pack(side="left", padx=(px(6), 0), pady=(px(1), 0))
        refs["pct"] = self._label(line, row["pct"], 10, bold=True)
        refs["pct"].pack(side="right")

        bar = tk.Canvas(parent, height=px(6), bg=pal["card"], highlightthickness=0)
        bar.value = (row["remaining"], row["color"])
        bar.pack(fill="x", pady=(px(4), px(3)))
        bar.bind("<Configure>", lambda e: self._draw_bar(bar))
        refs["bar"] = bar
        if row["when"]:
            refs["when"] = self._label(parent, row["when"], 8, "muted")
            refs["when"].pack(anchor="w")
        return refs

    def _draw_bar(self, canvas: tk.Canvas) -> None:
        width = canvas.winfo_width()
        if width <= 1:
            return
        remaining, color = canvas.value
        canvas.delete("all")
        h = int(canvas["height"])
        y, r = h / 2, h / 2
        canvas.create_line(r, y, width - r, y, width=h, fill=self.app.pal["track"], capstyle="round")
        if remaining > 0:
            x = r + (width - h) * remaining / 100
            canvas.create_line(r, y, max(x, r + 0.5), y, width=h, fill=color, capstyle="round")

    # ── 값만 고치기 ───────────────────────────────────────
    @staticmethod
    def _set(label: tk.Label | None, text: str) -> None:
        if label is not None and label.cget("text") != text:
            label.configure(text=text)

    def _apply(self, m: dict) -> None:
        for c in m["cards"]:
            refs = self.refs["cards"][c["id"]]
            self._set(refs.get("plan"), c["plan"])
            self._set(refs.get("meta"), c["meta"])
            for row in c["rows"]:
                r = refs["rows"][row["key"]]
                self._set(r["label"], row["label"])
                self._set(r.get("note"), row["note"])
                self._set(r["pct"], row["pct"])
                self._set(r.get("when"), row["when"])
                value = (row["remaining"], row["color"])
                if r["bar"].value != value:
                    r["bar"].value = value
                    self._draw_bar(r["bar"])
            if c["reset"]:
                self._set(refs.get("reset"), c["reset"][0])
            for label, text in zip(refs["extras"], c["extras"]):
                self._set(label, text)
            if c["problem"] is not None:
                self._set(refs.get("problem"), c["problem"])
            self._set(refs.get("hint"), c["hint"])
        self._set(self.refs.get("footer"), m["footer"])


def main() -> None:
    logging.basicConfig(filename=os.path.join(DATA_DIR, "app.log"), level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")
    sys.excepthook = lambda *exc: log.error("uncaught", exc_info=exc)
    if winutil.already_running():
        return
    App().run()
