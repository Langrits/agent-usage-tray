"""Codex 작업 중 지금 진행 중인 것: Codex 데스크톱 스레드와 다른 프로그램이 부른 codex exec.

기록: ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl
첫 줄 session_meta: id, cwd, originator("codex_exec" | "Codex Desktop"), source(서브에이전트면 부모 스레드 ID)
턴 시작·끝(task_started / task_complete)과 승인 요청이 기록에 남는다. 이 기록이 없는 로그는 보여 주지 않는다.
"""
from __future__ import annotations

import glob
import os

from ._files import JsonlCache, epoch, mtime
from .model import LIMITED, RUNNING, WAITING, Item, settle, short

CODEX_DIR = os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
HEAD_BYTES = 64 << 10     # 큰 기록은 앞(세션 정보·첫 요청)과
TAIL_BYTES = 1 << 20      # 끝(지금 상태)만 읽는다
# 승인 요청 뒤 이 이벤트가 오면 작업이 다시 진행 중인 것
PROGRESS = {"item_completed", "agent_message", "exec_command_begin", "exec_command_end",
            "function_call_output", "custom_tool_call_output", "patch_apply_begin", "patch_apply_end"}


class CodexLog:
    def __init__(self):
        self.id = None
        self.originator = None
        self.parent_thread = None
        self.updated = None
        self.first_prompt = None
        self.turn_open = False
        self.waiting = False
        self.limit_reached = False
        self.turn_at = self.wait_at = self.limit_at = None

    @property
    def state(self) -> str | None:
        if self.turn_open:
            return WAITING if self.waiting else RUNNING
        return LIMITED if self.limit_reached else None

    @property
    def since(self) -> float | None:
        return {RUNNING: self.turn_at, WAITING: self.wait_at, LIMITED: self.limit_at}.get(self.state)

    def gap(self) -> None:
        """앞부분과 끝부분 사이를 건너뛰었다: 그 사이에 턴이 몇 번 있었는지 모르니 시작 시각은 버린다."""
        self.turn_at = self.wait_at = self.limit_at = None

    def feed(self, rec: dict) -> None:
        ts = epoch(rec.get("timestamp"))
        if ts:
            self.updated = max(self.updated or 0, ts)
        payload = rec.get("payload") or {}
        if rec.get("type") == "session_meta":
            self.id = payload.get("id") or payload.get("session_id")
            self.originator = payload.get("originator")
            source = payload.get("source")
            if isinstance(source, dict) and isinstance(source.get("subagent"), dict):
                sub = source["subagent"]
                self.parent_thread = sub.get("thread_id") or sub.get("parent_thread_id")
            return
        kind = payload.get("type")
        if kind == "user_message" and not self.first_prompt:
            self.first_prompt = payload.get("message")
        elif kind == "message" and payload.get("role") == "user" and not self.first_prompt:
            # codex exec는 요청이 응답 기록(role=user)에만 남는다. 환경 정보·AGENTS.md 블록은 건너뛴다.
            for item in payload.get("content") or []:
                text = (item or {}).get("text") if isinstance(item, dict) else None
                if text and not text.lstrip().startswith("<") and "AGENTS.md" not in text[:60]:
                    self.first_prompt = text
                    break
        elif kind == "task_started":
            self.turn_open, self.waiting, self.limit_reached, self.turn_at = True, False, False, ts
        elif kind in ("task_complete", "turn_aborted"):
            self.turn_open, self.waiting = False, False
        elif kind in PROGRESS:
            self.waiting = False
        elif kind == "token_count":
            reached = bool((payload.get("rate_limits") or {}).get("rate_limit_reached_type"))
            if reached and not self.limit_reached:
                self.limit_at = ts
            self.limit_reached = reached
        elif isinstance(kind, str) and kind.endswith("approval_request"):
            if not self.waiting:
                self.wait_at = ts
            self.waiting = True


def collect(since: float, now: float, cache: JsonlCache) -> list[Item]:
    items = []
    for path in glob.glob(os.path.join(CODEX_DIR, "sessions", "*", "*", "*", "rollout-*.jsonl")):
        if mtime(path) < since:
            continue
        log = cache.get(path, CodexLog, first=HEAD_BYTES, last=TAIL_BYTES)
        if log is None or not log.id or log.parent_thread:      # 서브에이전트는 부모 스레드가 진행 중으로 보인다
            continue
        status = settle(log.state, log.updated, now)
        if status is None:
            continue
        items.append(Item(
            uid=f"codex:{log.id}", agent="codex", label="Codex exec" if log.originator == "codex_exec" else "Codex",
            title=short(log.first_prompt) or "Codex", status=status, since=log.since, updated_at=log.updated))
    return items
