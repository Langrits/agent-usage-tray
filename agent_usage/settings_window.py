"""설정 화면. 바꾸는 즉시 저장하고 반영한다."""
from __future__ import annotations

import os
import tkinter as tk
import webbrowser

from . import APP_NAME, REPO_URL, __version__, theme, winutil
from .i18n import t
from .providers import ALL
from .store import DATA_DIR

MODES = ("auto", "show", "hide")
LANGS = ("auto", "ko", "en")
LANG_NAMES = {"ko": "한국어", "en": "English"}


class SettingsWindow:
    def __init__(self, app):
        self.app = app
        self.pal = app.pal
        self.px = lambda v: int(round(v * app.scale))
        self.win = tk.Toplevel(app.root)
        self.win.configure(bg=self.pal["card"])
        self.win.resizable(False, False)
        self.status_labels: dict[str, tk.Label] = {}
        self.build()
        winutil.bring_to_front(self.win)

    def alive(self) -> bool:
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def focus(self) -> None:
        winutil.bring_to_front(self.win)

    # ── 공통 위젯 ─────────────────────────────────────────
    def _label(self, parent, text, size=9, color="text", bold=False, **kw) -> tk.Label:
        return tk.Label(parent, text=text, fg=self.pal[color], bg=self.pal["card"],
                        font=(theme.FONT, size, "bold") if bold else (theme.FONT, size), **kw)

    def _section(self, text) -> tk.Frame:
        self._label(self.body, text, 10, bold=True).pack(anchor="w", pady=(self.px(14), self.px(4)))
        frame = tk.Frame(self.body, bg=self.pal["card"])
        frame.pack(fill="x")
        return frame

    def _check(self, parent, text, value: bool, on_change) -> tk.Checkbutton:
        pal = self.pal
        var = tk.BooleanVar(value=value)
        cb = tk.Checkbutton(parent, text=text, variable=var, font=(theme.FONT, 9), anchor="w",
                            fg=pal["text"], bg=pal["card"], activebackground=pal["card"],
                            activeforeground=pal["text"], selectcolor=pal["bg"], highlightthickness=0,
                            command=lambda: on_change(var.get()))
        cb.var = var
        return cb

    def _spin(self, parent, value: int, lo: int, hi: int, on_change) -> tk.Spinbox:
        pal = self.pal
        var = tk.StringVar(value=str(value))

        def changed(*_):
            try:
                v = int(var.get())
            except ValueError:
                return
            if lo <= v <= hi:
                on_change(v)
        var.trace_add("write", changed)
        sp = tk.Spinbox(parent, from_=lo, to=hi, textvariable=var, width=4, font=(theme.FONT, 9),
                        fg=pal["text"], bg=pal["bg"], buttonbackground=pal["card"], insertbackground=pal["text"],
                        relief="flat", highlightthickness=1, highlightbackground=pal["border"],
                        highlightcolor=pal["border"])
        sp.var = var
        return sp

    def _choice(self, parent, options: list[tuple[str, str]], current: str, on_change) -> tk.OptionMenu:
        """options: [(값, 표시 이름)]"""
        pal = self.pal
        names = dict(options)
        var = tk.StringVar(value=names.get(current, options[0][1]))
        om = tk.OptionMenu(parent, var, *[name for _, name in options],
                           command=lambda name: on_change(next(k for k, n in options if n == name)))
        om.configure(font=(theme.FONT, 9), fg=pal["text"], bg=pal["bg"], activebackground=pal["hover"],
                     activeforeground=pal["text"], highlightthickness=0, relief="flat", bd=0,
                     padx=self.px(8), pady=self.px(2))
        om["menu"].configure(font=(theme.FONT, 9), fg=pal["text"], bg=pal["card"],
                             activebackground=pal["hover"], activeforeground=pal["text"])
        return om

    def _link(self, parent, text, command) -> tk.Label:
        lbl = self._label(parent, text, 9, "text2", cursor="hand2")
        lbl.bind("<Button-1>", lambda e: command())
        return lbl

    # ── 화면 ─────────────────────────────────────────────
    def build(self) -> None:
        for w in self.win.winfo_children():
            w.destroy()
        app, px, s = self.app, self.px, self.app.settings
        self.win.title(f"{t('s_title')} · {APP_NAME}")
        self.body = tk.Frame(self.win, bg=self.pal["card"])
        self.body.pack(fill="both", expand=True, padx=px(20), pady=(px(6), px(16)))

        # 서비스
        sec = self._section(t("s_services"))
        mode_names = [(m, t(f"s_mode_{m}")) for m in MODES]
        self.status_labels = {}
        for i, mod in enumerate(ALL):
            name = mod.TITLE + (f"  ({t('experimental')})" if mod.EXPERIMENTAL else "")
            self._label(sec, name, 9, bold=True).grid(row=i, column=0, sticky="w", pady=px(3))
            self.status_labels[mod.ID] = self._label(sec, "", 8, "muted")
            self.status_labels[mod.ID].grid(row=i, column=1, sticky="w", padx=(px(12), px(12)))
            self._choice(sec, mode_names, app.service_mode(mod.ID),
                         lambda mode, pid=mod.ID: self._set_mode(pid, mode)).grid(row=i, column=2, sticky="e")
        sec.columnconfigure(1, weight=1)
        self._label(self.body, t("s_mode_help"), 8, "muted").pack(anchor="w", pady=(px(2), 0))
        self.refresh_status()

        # 알림
        sec = self._section(t("s_alerts"))
        row = tk.Frame(sec, bg=self.pal["card"])
        row.pack(fill="x", pady=px(2))
        self._check(row, t("s_low"), bool(s["notify_low"]),
                    lambda v: app.set_setting("notify_low", v)).pack(side="left")
        self._spin(row, int(s["low_threshold"]), 5, 50,
                   lambda v: app.set_setting("low_threshold", v)).pack(side="left", padx=px(4))
        self._label(row, t("s_low_suffix")).pack(side="left")
        self._check(sec, t("s_reset"), bool(s["notify_reset"]),
                    lambda v: app.set_setting("notify_reset", v)).pack(anchor="w", pady=px(2))

        # 일반
        sec = self._section(t("s_general"))
        row = tk.Frame(sec, bg=self.pal["card"])
        row.pack(fill="x", pady=px(2))
        self._label(row, t("s_refresh")).pack(side="left", padx=(px(4), 0))
        self._spin(row, int(s["refresh_minutes"]), 2, 60,
                   lambda v: app.set_setting("refresh_minutes", v)).pack(side="left", padx=px(4))
        self._label(row, t("s_refresh_suffix")).pack(side="left")

        self._check(sec, t("s_auto_refresh"), bool(s["auto_refresh_login"]),
                    lambda v: app.set_setting("auto_refresh_login", v)).pack(anchor="w", pady=(px(6), 0))
        self._label(sec, t("s_auto_refresh_help"), 8, "muted", wraplength=px(380), justify="left") \
            .pack(anchor="w", padx=(px(24), 0))
        self._check(sec, t("s_autostart"), winutil.autostart_enabled(),
                    winutil.set_autostart).pack(anchor="w", pady=(px(6), 0))

        row = tk.Frame(sec, bg=self.pal["card"])
        row.pack(fill="x", pady=(px(8), 0))
        self._label(row, t("s_language")).pack(side="left", padx=(px(4), px(8)))
        langs = [("auto", t("s_lang_auto"))] + [(k, LANG_NAMES[k]) for k in LANGS[1:]]
        self._choice(row, langs, s["language"] or "auto", self._set_language).pack(side="left")

        # 아래쪽
        foot = tk.Frame(self.body, bg=self.pal["card"])
        foot.pack(fill="x", pady=(px(18), 0))
        self._label(foot, f"{APP_NAME} · {t('s_version', v=__version__)}", 8, "muted").pack(side="left")
        if REPO_URL:
            self._link(foot, "GitHub", lambda: webbrowser.open(REPO_URL)).pack(side="left", padx=px(8))
        close = tk.Button(foot, text=t("s_close"), command=self.win.destroy, font=(theme.FONT, 9),
                          fg=self.pal["text"], bg=self.pal["hover"], activebackground=self.pal["border"],
                          activeforeground=self.pal["text"], relief="flat", padx=px(14), pady=px(3))
        close.pack(side="right")
        self._link(foot, t("s_data"), lambda: os.startfile(DATA_DIR)).pack(side="right", padx=px(10))

    def refresh_status(self) -> None:
        for mod in ALL:
            lbl = self.status_labels.get(mod.ID)
            if lbl is not None:
                ok = mod.detect()
                lbl.configure(text=t("s_connected") if ok else t("s_not_connected"),
                              fg=self.pal["text2"] if ok else self.pal["muted"])

    def _set_mode(self, pid: str, mode: str) -> None:
        services = dict(self.app.settings["services"] or {})
        services[pid] = mode
        self.app.set_setting("services", services)

    def _set_language(self, choice: str) -> None:
        self.app.set_setting("language", choice)
        self.build()
