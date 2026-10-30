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
import sqlite3
import threading
import time

from .models import Usage

HOME_DATA_DIR = os.path.join(os.path.expanduser("~"), ".ai-usage-tray")
DATA_DIR = os.environ.get("AGENT_USAGE_DATA") or HOME_DATA_DIR
os.makedirs(DATA_DIR, exist_ok=True)


def _ensure_schema(con: sqlite3.Connection) -> None:
    con.execute("""create table if not exists samples (
        ts real, provider text, window text, label text, used real, resets_at real)""")
    con.execute("create index if not exists samples_ts on samples(ts)")
    # 같은 시각의 같은 한도는 한 번만 (작업 기록처럼 기준 시각이 그대로인 데이터가 반복 저장되지 않게)
    con.execute("""delete from samples where rowid not in (
        select min(rowid) from samples group by provider, window, ts)""")
    con.execute("create unique index if not exists samples_unique on samples(provider, window, ts)")
    # 예전 위치별로 마지막으로 합칠 때의 수정 시각. 합친 기록과 같은 DB·같은 커밋에 남겨서 어긋나지 않게 한다
    con.execute("create table if not exists legacy_merge (path text primary key, mtime real)")


def legacy_dirs() -> list[str]:
    """0.1.x가 쓰던 위치: AppData 원래 위치와, 스토어 앱 안에서 실행됐을 때 돌려진 사본들."""
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return []
    dirs = [os.path.join(local, "AgentUsage")]
    dirs += sorted(glob.glob(os.path.join(local, "Packages", "*", "LocalCache", "Local", "AgentUsage")))
    return [d for d in dict.fromkeys(dirs) if os.path.isdir(d)]


def migrate_legacy(target: str, sources: list[str]) -> None:
    """예전 위치들의 사용 기록을 target으로 합친다 (중복은 한 번만).

    예전 DB마다 마지막으로 합칠 때의 수정 시각을 target DB의 legacy_merge 표에 남기고, 그 시각과
    다를 때만 다시 합친다(새 기록이 생겼거나 예전 백업으로 되돌린 경우 모두). 기록과 이 표를 한 번에
    커밋하므로 도중에 꺼져도 둘이 어긋나지 않는다. target DB가 지워지면 표도 함께 사라져 처음부터 합친다.
    설정·알림 상태 파일은 target에 없을 때만 가장 최근에 바뀐 올바른 사본을 복사한다.
    """
    os.makedirs(target, exist_ok=True)
    new_db = os.path.join(target, "history.sqlite")
    con = sqlite3.connect(new_db)
    try:
        _ensure_schema(con)
        merged = dict(con.execute("select path, mtime from legacy_merge").fetchall())
        for src in sources:
            old = os.path.join(src, "history.sqlite")
            if not os.path.exists(old) or os.path.abspath(old) == os.path.abspath(new_db):
                continue
            mtime = os.path.getmtime(old)
            if merged.get(old) == mtime:
                continue
            try:
                ocon = sqlite3.connect(pathlib.Path(old).as_uri() + "?mode=ro", uri=True)
                try:
                    rows = ocon.execute("select ts, provider, window, label, used, resets_at from samples").fetchall()
                finally:
                    ocon.close()
            except sqlite3.Error:
                continue                     # 이번에 못 읽은 곳은 표에 남기지 않아 다음 시작 때 다시 시도한다
            con.executemany("insert or ignore into samples values (?,?,?,?,?,?)", rows)
            con.execute("insert or replace into legacy_merge values (?, ?)", (old, mtime))
        con.commit()
    finally:
        con.close()
    for name in ("settings.json", "notify_state.json"):
        dest = os.path.join(target, name)
        if os.path.exists(dest):
            continue
        found = [os.path.join(s, name) for s in sources if os.path.exists(os.path.join(s, name))]
        for src in sorted(found, key=os.path.getmtime, reverse=True):   # 최근 것부터, 깨진 사본은 건너뛴다
            if _copy_json_atomic(src, dest):
                break


def _copy_json_atomic(src: str, dest: str) -> bool:
    """올바른 설정 JSON(객체)일 때만 임시 파일에 써서 바꿔치기한다 (중간에 끊겨도 반쪽 파일이 남지 않게)."""
    try:
        with open(src, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):        # null·목록 등은 설정으로 쓸 수 없으니 다음 사본을 찾는다
            return False
        tmp = dest + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, dest)
        return True
    except (OSError, ValueError):
        return False


def _merge_legacy() -> None:
    if os.environ.get("AGENT_USAGE_DATA"):
        return                       # 위치를 직접 정한 경우(테스트 등)는 건드리지 않는다
    try:
        migrate_legacy(DATA_DIR, legacy_dirs())
    except (OSError, sqlite3.Error):
        pass                         # 합치기에 실패해도 앱은 새 위치로 계속 동작한다 (다음 시작 때 다시 시도)


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
    "history_range_hours": 24,  # 사용 기록 패널 기간
    "history_open": False,      # 팝업 옆 사용 기록 패널을 켜 두었는지
}


class JsonFile:
    def __init__(self, name: str, defaults: dict | None = None):
        self.path = os.path.join(DATA_DIR, name)
        self.data = dict(defaults or {})
        try:
            with open(self.path, encoding="utf-8") as f:
                loaded = json.load(f)
        except (FileNotFoundError, ValueError):
            return
        if isinstance(loaded, dict):          # 내용이 null 등이면 기본값으로 시작한다 (실행이 멈추지 않게)
            self.data.update(loaded)

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
