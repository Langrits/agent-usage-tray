"""Cursor 에디터 안의 에이전트 대화(composer) 중 지금 진행 중인 것.

기록: %APPDATA%/Cursor/User/globalStorage/state.vscdb
- composerHeaders 표: 대화 이름·폴더·보관 여부, 승인·계획 대기(hasBlockingPendingActions, hasPendingPlan)
- cursorDiskKV 의 composerData:<ID>: 진행 상태(status == "generating")
진행 상태가 기록에 없는 대화와 Cursor CLI 대화(상태가 남지 않음)는 보여 주지 않는다.
"""
from __future__ import annotations

import json
import os
import sqlite3
from urllib.parse import unquote, urlparse

from ._files import epoch, sqlite_ro_uri
from .model import RUNNING, WAITING, Item, settle, short

STATE_DB = os.path.join(os.environ.get("APPDATA", ""), "Cursor", "User", "globalStorage", "state.vscdb")


def _path_from_uri(uri: str | None) -> str | None:
    if not uri or not uri.startswith("file:"):
        return None
    path = unquote(urlparse(uri).path)
    if len(path) > 2 and path[0] == "/" and path[2] == ":":   # /c:/... → c:/...
        path = path[1:]
    return os.path.normpath(path)


def _rows(since: float) -> list[tuple]:
    """(대화 ID, 갱신 시각 ms, 보관 여부, 서브에이전트 여부, 머리글 JSON, 진행 상태)"""
    con = sqlite3.connect(sqlite_ro_uri(STATE_DB), uri=True, timeout=2)    # Cursor가 쓰는 중이면 잠깐 기다린다
    try:
        rows = con.execute(
            "select composerId, lastUpdatedAt, isArchived, isSubagent, value "
            "from composerHeaders where lastUpdatedAt >= ?", (int(since * 1000),)).fetchall()
        out = []
        for row in rows:
            try:          # 진행 상태 조회가 실패해도 승인 대기는 살린다
                kv = con.execute("select value from cursorDiskKV where key=?", (f"composerData:{row[0]}",)).fetchone()
                state = json.loads(kv[0]).get("status") if kv else None
            except (sqlite3.Error, TypeError, ValueError, AttributeError):
                state = None
            out.append((*row, state))
        return out
    finally:
        con.close()


def collect(since: float, now: float, cache=None) -> list[Item]:
    if not os.path.exists(STATE_DB):
        return []
    items = []
    for cid, updated_ms, archived, is_sub, value, state in _rows(since):
        if archived or is_sub:
            continue
        try:
            head = json.loads(value)
        except (TypeError, ValueError):
            head = {}
        if not isinstance(head, dict) or head.get("isDraft"):
            continue
        if head.get("hasBlockingPendingActions") or head.get("hasPendingPlan"):
            found = WAITING
        elif state == "generating":
            found = RUNNING
        else:
            continue
        updated = epoch(updated_ms)
        status = settle(found, updated, now)
        if status is None:
            continue
        folder = _path_from_uri((head.get("workspaceIdentifier") or {}).get("uri"))
        items.append(Item(
            uid=f"cursor:{cid}", agent="cursor", label="Cursor",
            title=short(head.get("name") or head.get("subtitle")) or "Cursor",
            status=status, since=None, updated_at=updated,
            open_url=f"cursor://file/{folder.replace(os.sep, '/')}" if folder else None))
    return items
