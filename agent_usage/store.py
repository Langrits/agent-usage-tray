"""설정·알림 상태(JSON)와 사용 기록(SQLite). 위치: %USERPROFILE%/.ai-usage-tray

0.1.x는 %LOCALAPPDATA%/AgentUsage를 썼다. 그런데 스토어(MSIX) 앱 안에서 실행되면 Windows가 AppData 쓰기를
그 앱 전용 폴더(%LOCALAPPDATA%/Packages/<앱>/LocalCache/Local/AgentUsage)로 돌려서, 앱을 켠 방법에 따라
기록이 두 곳에 나뉘었다. 사용자 폴더 바로 아래는 이 가상화가 적용되지 않는다.
시작할 때 예전 위치들의 기록을 합쳐 온다 (예전 폴더는 지우지 않는다).
"""
from __future__ import annotations

import glob
import json
import os
import pathlib
import shutil
import sqlite3
import threading
import time

from .models import Usage

HOME_DATA_DIR = os.path.join(os.path.expanduser("~"), ".ai-usage-tray")
DATA_DIR = os.environ.get("AGENT_USAGE_DATA") or HOME_DATA_DIR
os.makedirs(DATA_DIR, exist_ok=True)
MERGE_STATE = ".legacy-merge.json"      # 예전 위치별로 마지막으로 합친 시점


def _ensure_schema(con: sqlite3.Connection) -> None:
    con.execute("""create table if not exists samples (
        ts real, provider text, window text, label text, used real, resets_at real)""")
    con.execute("create index if not exists samples_ts on samples(ts)")
    # 같은 시각의 같은 한도는 한 번만 (작업 기록처럼 기준 시각이 그대로인 데이터가 반복 저장되지 않게)
    con.execute("""delete from samples where rowid not in (
        select min(rowid) from samples group by provider, window, ts)""")
    con.execute("create unique index if not exists samples_unique on samples(provider, window, ts)")


def legacy_dirs() -> list[str]:
    """0.1.x가 쓰던 위치: AppData 원래 위치와, 스토어 앱 안에서 실행됐을 때 돌려진 사본들."""
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return []
    dirs = [os.path.join(local, "AgentUsage")]
    dirs += sorted(glob.glob(os.path.join(local, "Packages", "*", "LocalCache", "Local", "AgentUsage")))
    return [d for d in dict.fromkeys(dirs) if os.path.isdir(d)]


def migrate_legacy(target: str, sources: list[str], merged: dict | None = None) -> dict:
    """예전 위치들의 사용 기록을 target으로 합친다 (중복은 한 번만).

    merged: {예전 history.sqlite 경로: 마지막으로 합칠 때의 수정 시각}. 그 뒤로 바뀐 것만 다시 합친다.
    설정·알림 상태 파일은 target에 없을 때만 가장 최근에 바뀐 것을 복사한다.
    돌려주는 값은 갱신된 merged.
    """
    merged = dict(merged or {})
    os.makedirs(target, exist_ok=True)
    new_db = os.path.join(target, "history.sqlite")
    if not os.path.exists(new_db):
        merged = {}                  # 새 DB를 처음부터 만드는 경우: 예전에 합친 기록도 다시 가져와야 한다
    con = sqlite3.connect(new_db)
    try:
        _ensure_schema(con)
        for src in sources:
            old = os.path.join(src, "history.sqlite")
            if not os.path.exists(old) or os.path.abspath(old) == os.path.abspath(new_db):
                continue
            mtime = os.path.getmtime(old)
            if merged.get(old) == mtime:     # 지난번과 똑같으면 건너뛴다 (백업으로 되돌려 시각이 줄어도 다시 합침)
                continue
            try:
                ocon = sqlite3.connect(pathlib.Path(old).as_uri() + "?mode=ro", uri=True)
                try:
                    rows = ocon.execute("select ts, provider, window, label, used, resets_at from samples").fetchall()
                finally:
                    ocon.close()
            except sqlite3.Error:
                continue
            con.executemany("insert or ignore into samples values (?,?,?,?,?,?)", rows)
            merged[old] = mtime
        con.commit()
    finally:
        con.close()
    for name in ("settings.json", "notify_state.json"):
        dest = os.path.join(target, name)
        found = [os.path.join(s, name) for s in sources if os.path.exists(os.path.join(s, name))]
        if found and not os.path.exists(dest):
            shutil.copy2(max(found, key=os.path.getmtime), dest)
    return merged


def _merge_legacy() -> None:
    if os.environ.get("AGENT_USAGE_DATA"):
        return                       # 위치를 직접 정한 경우(테스트 등)는 건드리지 않는다
    state_path = os.path.join(DATA_DIR, MERGE_STATE)
    try:
        with open(state_path, encoding="utf-8") as f:
            merged = json.load(f)
    except (OSError, ValueError):
        merged = {}
    try:
        updated = migrate_legacy(DATA_DIR, legacy_dirs(), merged)
        if updated != merged:
            tmp = state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(updated, f, ensure_ascii=False, indent=1)
            os.replace(tmp, state_path)
    except (OSError, sqlite3.Error):
        pass                         # 합치기나 상태 저장에 실패해도 앱은 새 위치로 계속 동작한다 (다음 시작 때 다시 시도)


_merge_legacy()

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
    "history_range_hours": 168,
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
        _ensure_schema(self.con)
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
