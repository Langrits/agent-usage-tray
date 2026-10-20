"""부족·초기화·재설정권 알림 판단. 같은 알림을 반복하지 않도록 상태를 파일에 남긴다."""
from __future__ import annotations

import time

from .format import countdown
from .i18n import t
from .models import Usage
from .store import JsonFile


class Notifier:
    def __init__(self, settings: JsonFile, send):
        self.settings = settings
        self.send = send                      # send(title, message)
        self.state = JsonFile("notify_state.json")

    def check(self, usage: Usage) -> None:
        if not usage.ok:
            return
        now = time.time()
        threshold = self.settings["low_threshold"]
        for w in usage.windows:
            if w.used_pct is None:
                continue
            key = f"{usage.provider}:{w.key}"
            prev = self.state[key]
            remaining = 100 - w.used_pct
            name = f"{usage.title} {w.label}"

            if prev and self.settings["notify_reset"]:
                passed = prev.get("resets_at") and now >= prev["resets_at"] - 60
                dropped = w.used_pct < prev["used"] - 5
                big_drop = w.used_pct < prev["used"] - 20    # 재설정권을 쓴 경우 등
                if (passed and dropped) or big_drop:
                    self.send(t("n_reset_title"), t("n_reset", name=name, r=f"{remaining:.0f}"))

            low_cycle = (prev or {}).get("low_cycle")
            if self.settings["notify_low"] and remaining <= threshold:
                if low_cycle != w.resets_at:
                    when = countdown(w.resets_at)
                    self.send(t("n_low_title"), t("n_low", name=name, r=f"{remaining:.0f}")
                              + (t("n_low_when", when=when) if when else ""))
                    low_cycle = w.resets_at
            elif remaining > threshold:
                low_cycle = None

            self.state.data[key] = {"used": w.used_pct, "resets_at": w.resets_at, "low_cycle": low_cycle}

        if usage.reset_credits is not None:
            key = f"{usage.provider}:reset_credits"
            prev_count = self.state[key]
            if self.settings["notify_reset"] and prev_count is not None and usage.reset_credits.count > prev_count:
                self.send(t("n_rc_title"), t("n_rc", title=usage.title, n=usage.reset_credits.count))
            self.state.data[key] = usage.reset_credits.count
        self.state.save()
