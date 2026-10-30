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

from . import APP_NAME, __version__, activity, i18n, theme, winutil
from . import format as fmt
from .activity_panel import ActivityPanel
from .history_panel import HistoryPanel
from .i18n import t
from .models import Usage
from .notify import Notifier
from .providers import ALL, BY_ID, Options, fetch_all
from .settings_window import SettingsWindow
from .store import DATA_DIR, History, settings

GLYPH = {"refresh": "", "chart": "", "pin": "", "unpin": "",
         "close": "", "settings": ""}
GLYPH["activity"] = ""      # 목록 (작업 현황)
ACTIVITY_EVERY_S = 15             # 팝업이 열려 있는 동안 작업 현황을 다시 모으는 간격
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
        self.activity: activity.Snapshot | None = None    # 마지막으로 모은 작업 현황
        self._activity_busy = False
        self.fetching = False
        self._refresh_again = False
        self._quitting = False
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
            self.popup.rebuild_panels()
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
            item(lambda _: t("menu_history"), lambda: self.post("history"),
                 checked=lambda _: bool(self.settings["history_open"])),
            item(lambda _: t("menu_activity"), lambda: self.post("activity"),
                 checked=lambda _: bool(self.settings["activity_open"])),
            item(lambda _: t("menu_settings"), lambda: self.post("settings")),
            pystray.Menu.SEPARATOR,
            item(f"{APP_NAME} v{__version__}", lambda: None, enabled=False),
            item(lambda _: t("menu_quit"), lambda: self.post("quit")),
        )

    def post(self, *msg) -> None:
        self.q.put(msg)

    def _poll(self) -> None:
        """메시지 하나가 실패해도 다음 메시지와 다음 폴링은 계속된다."""
        try:
            while True:
                try:
                    msg = self.q.get_nowait()
                except queue.Empty:
                    break
                try:
                    self._handle(*msg)
                except Exception:
                    log.exception("handling %r failed", msg[0] if msg else None)
        finally:
            if not self._quitting:
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
        elif kind == "history":
            if not self.popup.visible:            # 트레이 메뉴에서 켜면 팝업과 함께 보여준다
                self.settings["history_open"] = True
                self.icon.update_menu()
                self.popup.show()
            else:
                self.popup.toggle_history()
        elif kind == "activity":
            if not self.popup.visible:
                self.settings["activity_open"] = True
                self.icon.update_menu()
                self.popup.show()
            else:
                self.popup.toggle_activity()
        elif kind == "activity_result":
            self._on_activity(arg)
        elif kind == "settings":
            self.open_settings()
        elif kind == "quit":
            self._quitting = True
            self.icon.stop()
            self.root.destroy()

    # ── 조회 ─────────────────────────────────────────────
    def refresh(self) -> None:
        if self.fetching:
            self._refresh_again = True     # 조회 중 바뀐 설정은 끝나는 대로 다시 조회해 반영
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
        try:
            for pid, u in results.items():
                self.latest[pid] = u
                if not u.ok:
                    log.info("%s: %s", pid, u.error)
                    continue
                self.good[pid] = u
                for step in (self.history.add, self.notifier.check):   # 기록·알림 저장 실패가 화면 갱신을 막지 않게
                    try:
                        step(u)
                    except Exception:
                        log.exception("%s failed for %s", step.__name__, pid)
            self._flush_notes()
            self._update_tray()
            self.popup.render_if_visible()
            if self.popup.history.alive():
                self.popup.history.redraw()
            if self.settings_win and self.settings_win.alive():
                self.settings_win.refresh_status()
        finally:
            if self._refresh_again:
                self._refresh_again = False
                self.schedule_refresh(1)
            else:
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

    # ── 작업 현황 (기록 파일을 읽으므로 별도 스레드에서) ──────────
    def refresh_activity(self) -> None:
        if self._activity_busy:
            return
        self._activity_busy = True

        def work():
            try:
                snap = activity.collect()
            except Exception:
                log.exception("activity collect failed")
                snap = None
            self.post("activity_result", snap)
        threading.Thread(target=work, daemon=True).start()

    def _on_activity(self, snap) -> None:
        self._activity_busy = False
        if snap is None:
            return
        self.activity = snap
        self.popup.update_activity_count()
        if self.popup.activity.alive():
            self.popup.activity.redraw()

    def _activity_tick(self) -> None:
        if self.popup.visible:          # 팝업을 볼 때만 모은다 (머리글 버튼의 개수·패널)
            self.refresh_activity()
        self.root.after(ACTIVITY_EVERY_S * 1000, self._activity_tick)

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
        self.root.after(ACTIVITY_EVERY_S * 1000, self._activity_tick)
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
        self._scrolling = False
        self.history = HistoryPanel(self)      # 팝업 옆에 붙는 사용 기록 패널
        self.activity = ActivityPanel(self)    # 그 바깥쪽에 붙는 작업 현황 패널

    def px(self, v: float) -> int:
        return int(round(v * self.app.scale))

    @property
    def pinned(self) -> bool:
        return bool(self.app.settings["pinned"])

    @property
    def scrolling(self) -> bool:
        return self._scrolling

    def card_layout(self) -> dict:
        """서비스 ID → (카드 프레임, 사용량 줄 수). 기록 패널이 그래프 위치·크기를 정하는 데 쓴다."""
        return {pid: (refs["frame"], len(refs.get("rows", {})))
                for pid, refs in self.refs.get("cards", {}).items() if "frame" in refs}

    # ── 기록 패널 ─────────────────────────────────────────
    def toggle_history(self) -> None:
        on = not self.app.settings["history_open"]
        self.app.settings["history_open"] = on
        self.app.icon.update_menu()
        self._style_toggles()
        if on and self.visible:
            self.history.show()
        else:
            self.history.hide()
        self.activity.follow()                 # 작업 현황 패널은 기록 패널 바깥쪽에 붙는다

    def _style_toggles(self) -> None:
        """패널 버튼 강조만 바꾼다 (팝업 전체를 다시 만들면 깜빡인다)."""
        pal = self.app.pal
        for name in ("history", "activity"):
            b = self.refs.get("btn_" + name)
            if b:
                b.configure(fg=pal["text"] if self.app.settings[name + "_open"] else pal["text2"])

    def toggle_activity(self) -> None:
        on = not self.app.settings["activity_open"]
        self.app.settings["activity_open"] = on
        self.app.icon.update_menu()
        self._style_toggles()
        if on and self.visible:
            self.activity.show()
            self.app.refresh_activity()
        else:
            self.activity.hide()

    def rebuild_panels(self) -> None:
        """언어를 바꾸면 패널 머리글 글자도 바뀌어야 해서 새로 만든다."""
        was_open = self.history.alive(), self.activity.alive()
        for panel in (self.history, self.activity):
            if panel.win:
                panel.win.destroy()
        self.history = HistoryPanel(self)
        self.activity = ActivityPanel(self)
        if was_open[0]:
            self.history.show()
        if was_open[1]:
            self.activity.show()

    def update_activity_count(self) -> None:
        """머리글 작업 현황 버튼 옆 숫자: 진행 중인 작업 수 (답을 기다리는 게 있으면 노란색)."""
        label = self.refs.get("activity_count")
        if not label:
            return
        items = self.app.activity.items if self.app.activity else []
        waiting = any(i.status == activity.WAITING for i in items)
        label.configure(text=str(len(items)) if items else "",
                        fg=theme.STATUS["warning"] if waiting else self.app.pal["text2"])

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
        outer = tk.Frame(self.win, bg=pal["bg"])
        outer.pack(fill="both", expand=True, padx=1, pady=1)
        # 화면보다 길어지면 스크롤된다 (카드가 많거나 화면 배율이 클 때)
        self.canvas = tk.Canvas(outer, bg=pal["bg"], highlightthickness=0, bd=0)
        self.scrollbar = tk.Scrollbar(outer, orient="vertical", command=self.canvas.yview, width=self.px(10))
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.frame = tk.Frame(self.canvas, bg=pal["bg"])
        self._frame_id = self.canvas.create_window(0, 0, window=self.frame, anchor="nw")
        self.win.bind_all("<MouseWheel>", self._on_wheel, add="+")

    def show(self) -> None:
        if not self.win:
            self._create()
        self.visible = True
        self.render()
        self.win.deiconify()
        self.win.lift()
        winutil.round_corners(self.win)
        if self.app.settings["history_open"]:
            self.history.show()
        if self.app.settings["activity_open"]:
            self.activity.show()
        self.app.refresh_activity()            # 머리글 버튼의 개수를 바로 맞춘다
        if not self.pinned:
            self.win.after(50, self.win.focus_force)

    def hide(self) -> None:
        self.history.hide()
        self.activity.hide()
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
        w = self.px(self.WIDTH)
        pos = self.app.settings["pin_pos"] if self.pinned else None
        left, top, right, bottom = winutil.work_area_at(pos[0] + w // 2, pos[1]) if pos else winutil.work_area()
        content = self.frame.winfo_reqheight()
        h = min(content, bottom - top - self.px(24)) + 2          # +2: 테두리
        scrolling = content + 2 > h
        if scrolling != self._scrolling:
            self._scrolling = scrolling
            if scrolling:
                self.scrollbar.pack(side="right", fill="y")
            else:
                self.scrollbar.pack_forget()
                self.canvas.yview_moveto(0)
        inner_w = w - 2 - (self.scrollbar.winfo_reqwidth() if scrolling else 0)
        self.canvas.itemconfigure(self._frame_id, width=inner_w)
        self.canvas.configure(width=inner_w, height=h - 2, scrollregion=(0, 0, inner_w, content))
        if pos:
            x = min(max(pos[0], left), right - w)
            y = min(max(pos[1], top), bottom - h)
        else:
            x, y = right - w - self.px(12), bottom - h - self.px(12)
        if (w, h, x, y) != self._geom:       # 크기·위치가 바뀔 때만 옮긴다
            self._geom = (w, h, x, y)
            self.win.geometry(f"{w}x{h}+{x}+{y}")
        self.history.follow()
        self.activity.follow()

    def _on_wheel(self, e) -> None:
        if not (self._scrolling and self.visible):
            return
        try:
            inside = e.widget.winfo_toplevel() == self.win     # 설정·그래프 창의 휠은 무시
        except (AttributeError, tk.TclError):
            inside = False
        if inside:
            self.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")

    # 고정 상태에서는 머리글을 끌어 옮길 수 있다
    def _drag_start(self, e) -> None:
        self._drag = (e.x_root - self.win.winfo_x(), e.y_root - self.win.winfo_y())

    def _drag_move(self, e) -> None:
        if self.pinned:
            self.win.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")
            self.history.follow()
            self.activity.follow()

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
        self._style_toggles()                  # 숨겨진 동안 트레이 메뉴로 패널을 켠 경우에도 버튼 강조를 맞춘다
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

    def _activity_button(self, parent) -> tk.Frame:
        """작업 현황 버튼: 아이콘 옆에 진행 중인 작업 수를 붙인다."""
        pal, px = self.app.pal, self.px
        active = bool(self.app.settings["activity_open"])
        box = tk.Frame(parent, bg=pal["bg"], cursor="hand2")
        icon = tk.Label(box, text=GLYPH["activity"], font=(theme.ICON_FONT, 10), bg=pal["bg"],
                        fg=pal["text"] if active else pal["text2"], padx=px(6), pady=px(4))
        count = tk.Label(box, text="", font=(theme.FONT, 8, "bold"), bg=pal["bg"], padx=0)
        icon.pack(side="left")
        count.pack(side="left", padx=(0, px(4)))
        parts = (box, icon, count)

        def paint(bg):
            for w in parts:
                w.configure(bg=bg)

        def leave(e):          # 아이콘과 숫자 사이를 오갈 때는 강조를 유지한다
            if box.winfo_containing(e.x_root, e.y_root) not in parts:
                paint(pal["bg"])
        for w in parts:
            w.bind("<Enter>", lambda e: paint(pal["hover"]))
            w.bind("<Leave>", leave)
            w.bind("<Button-1>", lambda e: self.win.after_idle(self.toggle_activity))
        self.refs["activity_count"] = count
        self.refs["btn_activity"] = icon
        self.update_activity_count()
        return box

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
                                   (GLYPH["chart"], self.toggle_history, bool(self.app.settings["history_open"])),
                                   (None, None, None),
                                   (GLYPH["refresh"], self.app.refresh, False)):
            b = self._activity_button(head) if glyph is None else self._icon_button(head, glyph, cmd, active)
            b.pack(side="right")
            if cmd == self.toggle_history:
                self.refs["btn_history"] = b
        for w in (head, title):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<ButtonRelease-1>", self._drag_end)

    def _build_card(self, parent, c: dict) -> dict:
        pal, px = self.app.pal, self.px
        refs: dict = {"rows": {}, "extras": []}
        card = tk.Frame(parent, bg=pal["card"], highlightthickness=1, highlightbackground=pal["border"])
        card.pack(fill="x", padx=px(10), pady=px(4))
        refs["frame"] = card
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
