"""설정·알림 상태(JSON)와 사용 기록(SQLite). 위치: %LOCALAPPDATA%/AgentUsage"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

from .models import Usage

DATA_DIR = os.environ.get("AGENT_USAGE_DATA") or \
    os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "AgentUsage")
os.makedirs(DATA_DIR, exist_ok=True)

DEFAULTS = {
    "services": {},             # {서비스 ID: "auto" | "show" | "hide"}, 없으면 auto
    "refresh_minutes": 5,
    "low_threshold": 20,        # 남은 양이 이 % 이하로 내려가면 알림
    "notify_low": True,
    "notify_reset": True,
    "auto_refresh_login": True, # 만료된 Claude·Gemini CLI 로그인을 갱신해 다시 저장
    "language": "auto",         # "auto" | "ko" | "en"
    "pinned": False,
    "pin_pos": None,            # [x, y]
    "history_range_hours": 24,  # 사용 기록 패널 기간
    "history_open": False,      # 팝업 옆 사용 기록 패널을 켜 두었는지
}


class JsonFile:
    def __init__(self, name: str, defaults: dict | None = None):
        self.path = os.path.join(DATA_DIR, name)
        self.data = dict(defaults or {})
        try:
            with open(self.path, encoding="utf-8") as f:
                self.data.update(json.load(f))
        except (FileNotFoundError, ValueError):
            pass

    def __getitem__(self, key):
        return self.data.get(key)

    def __setitem__(self, key, value):
        self.data[key] = value
        self.save()

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)


def settings() -> JsonFile:
    return JsonFile("settings.json", DEFAULTS)


class History:
    def __init__(self):
        self.lock = threading.Lock()
        self.con = sqlite3.connect(os.path.join(DATA_DIR, "history.sqlite"), check_same_thread=False)
        self.con.execute("""create table if not exists samples (
            ts real, provider text, window text, label text, used real, resets_at real)""")
        self.con.execute("create index if not exists samples_ts on samples(ts)")
        # 같은 시각의 같은 한도는 한 번만 (작업 기록처럼 기준 시각이 그대로인 데이터가 반복 저장되지 않게)
        self.con.execute("""delete from samples where rowid not in (
            select min(rowid) from samples group by provider, window, ts)""")
        self.con.execute("create unique index if not exists samples_unique on samples(provider, window, ts)")
        self.con.commit()

    def add(self, usage: Usage) -> None:
        if not usage.ok:
            return
        rows = [(usage.data_at, usage.provider, w.key, w.label, w.used_pct, w.resets_at)
                for w in usage.windows if w.used_pct is not None]
        with self.lock:
            self.con.executemany("insert or ignore into samples values (?,?,?,?,?,?)", rows)
            self.con.commit()

    def series(self, since_hours: float) -> dict[tuple[str, str], list[tuple[float, float, str]]]:
        since = time.time() - since_hours * 3600
        with self.lock:
            rows = self.con.execute(
                "select provider, window, label, ts, used from samples where ts>=? order by ts", (since,)).fetchall()
        out: dict[tuple[str, str], list] = {}
        for provider, window, label, ts, used in rows:
            out.setdefault((provider, window), []).append((ts, used, label))
        return out

    def prune(self, keep_days: int = 90) -> None:
        with self.lock:
            self.con.execute("delete from samples where ts<?", (time.time() - keep_days * 86400,))
            self.con.commit()
