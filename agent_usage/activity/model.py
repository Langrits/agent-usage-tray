"""작업 현황 데이터 구조: 지금 진행 중인 작업만 다룬다."""
from __future__ import annotations

from dataclasses import dataclass

RUNNING, WAITING, LIMITED, STALLED = "running", "waiting", "limited", "stalled"
# 사람이 할 일이 있는 것부터: 답 기다림 → 한도로 멈춤 → 작업 중 → 기록이 끊김
ORDER = {WAITING: 0, LIMITED: 1, RUNNING: 2, STALLED: 3}

STALL_S = 10 * 60      # 작업 중인데 이만큼 기록이 없으면 '멈춘 듯' (단정하지 않고 기록 없는 시간만 보여 준다)
GONE_S = 2 * 3600      # 그보다 오래 기록이 없으면 끝난 것으로 보고 뺀다 (앱을 닫은 경우 등)
KEEP_S = 24 * 3600     # 답 기다림·한도 중단은 하루 동안만 남긴다


@dataclass
class Item:
    uid: str                     # "claude:<id>" 처럼 서비스 안에서 고유한 ID
    agent: str                   # "claude" | "codex" | "cursor" (한도 초기화 시각을 찾을 서비스)
    label: str                   # 화면에 보이는 앱 이름 ("Claude", "Codex exec" 등)
    title: str
    status: str
    since: float | None          # 지금 상태가 시작된 시각 (기록에 없으면 None)
    updated_at: float | None     # 기록이 마지막으로 남은 시각
    open_url: str | None = None  # 누르면 여는 주소 (그 대화, 없으면 프로젝트)


def settle(state: str | None, updated: float | None, now: float) -> str | None:
    """기록에 남은 상태 → 보여 줄 상태. 끝났거나 너무 오래된 것은 None."""
    if state is None or not updated:
        return None
    idle = now - updated
    if state == RUNNING:
        if idle > GONE_S:
            return None
        return STALLED if idle > STALL_S else RUNNING
    return state if idle <= KEEP_S else None


def short(text: str | None, n: int = 60) -> str:
    """제목용 한 줄 요약."""
    if not text:
        return ""
    line = " ".join(text.split())
    return line if len(line) <= n else line[: n - 1] + "…"
