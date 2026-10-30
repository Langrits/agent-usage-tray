"""작업 현황: 지금 진행 중인 Claude·Codex·Cursor 작업을 이 PC의 기록 파일에서 모은다.

앱이 기록에 직접 남긴 신호(턴 시작·끝, 질문·승인 대기, 한도 도달)만 쓰고, 짐작해야 하는 것은 보여 주지 않는다.
네트워크·로그인 정보는 쓰지 않는다.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from . import claude, codex, cursor
from ._files import JsonlCache
from .model import KEEP_S, LIMITED, ORDER, RUNNING, STALLED, WAITING, Item

__all__ = ["LIMITED", "RUNNING", "STALLED", "WAITING", "Item", "Snapshot", "collect"]

log = logging.getLogger("agent_usage")
_cache = JsonlCache()
SOURCES = (("Claude", claude), ("Codex", codex), ("Cursor", cursor))


@dataclass
class Snapshot:
    items: list[Item]                       # 사람이 할 일이 있는 것부터
    errors: list[str] = field(default_factory=list)    # 기록을 읽지 못한 앱 이름
    taken_at: float = field(default_factory=time.time)


def collect() -> Snapshot:
    now = time.time()
    since = now - KEEP_S
    items: list[Item] = []
    errors = []
    for name, mod in SOURCES:
        try:
            items += mod.collect(since, now, _cache)
        except Exception:      # 한 앱의 기록 형식이 바뀌어도 나머지는 보여 준다
            log.exception("activity %s", name)
            errors.append(name)
    items.sort(key=lambda i: (ORDER.get(i.status, 9), -(i.updated_at or 0)))
    _cache.prune()             # 이번에 안 쓴 파일 요약은 버린다
    return Snapshot(items, errors, now)
