"""Claude Code 대화 중 지금 진행 중인 것.

- 대화 기록: ~/.claude/projects/<프로젝트>/<세션 ID>.jsonl
- 서브에이전트 기록(<세션 ID>/subagents/agent-*.jsonl)은 따로 보여 주지 않고, 상위 대화가 아직 일하는 중이라는 신호로만 쓴다.
- 데스크톱 앱 세션 정보: %APPDATA%/Claude/claude-code-sessions/**/local_<id>.json (제목, 보관 여부, 사람 대기)
"""
from __future__ import annotations

import glob
import os

from ._files import JsonlCache, epoch, mtime, read_json
from .model import LIMITED, RUNNING, WAITING, Item, settle, short

HOME = os.path.expanduser("~")
PROJECTS = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(HOME, ".claude"), "projects")
DESKTOP_META = os.path.join(os.environ.get("APPDATA", ""), "Claude", "claude-code-sessions")

TAIL_BYTES = 1 << 20      # 큰 기록은 끝 1MB만 읽는다 (지금 상태와 제목은 끝부분에 남는다)
WAIT_TOOLS = {"AskUserQuestion", "ExitPlanMode"}     # 사용자 답을 받아야 끝나는 도구
WRAP_UP = "mcp__ccd_turn__wrap_up"                    # 데스크톱 앱: blocked면 사용자 결정을 기다리며 턴을 끝낸다


class ClaudeLog:
    """대화 기록 한 파일의 요약. 줄이 추가될 때마다 feed로 갱신된다.

    state: RUNNING(턴 진행 중) | WAITING(질문·결정 대기) | LIMITED(한도 오류로 멈춤) | None(턴 끝남)
    큰 기록은 끝부분만 읽으므로(TAIL_BYTES), 턴 시작을 못 본 경우 since는 None이다.
    """

    def __init__(self):
        self.cwd = None
        self.updated = None
        self.custom_title = None
        self.last_prompt = None
        self.pending: dict[str, str] = {}   # 결과가 아직 없는 도구 호출 ID → 도구 이름
        self.blocked = False                # 결정을 기다리며 턴을 끝내는 중
        self.state = None
        self.turn_at = None                 # 이번 턴을 시작한 요청 시각
        self.wait_at = None
        self.limit_at = None

    @property
    def since(self) -> float | None:
        return {RUNNING: self.turn_at, WAITING: self.wait_at, LIMITED: self.limit_at}.get(self.state)

    def _set(self, state: str | None, ts: float | None) -> None:
        if state == self.state:
            return
        if state == WAITING:
            self.wait_at = ts
        elif state == LIMITED:
            self.limit_at = ts
        self.state = state

    def feed(self, rec: dict) -> None:
        ts = epoch(rec.get("timestamp"))
        if ts:
            self.updated = max(self.updated or 0, ts)
        self.cwd = self.cwd or rec.get("cwd")
        kind = rec.get("type")
        if kind == "custom-title":
            self.custom_title = rec.get("customTitle") or self.custom_title
        elif kind == "last-prompt":
            self.last_prompt = rec.get("lastPrompt") or self.last_prompt
        elif kind == "user":
            self._user(rec, ts)
        elif kind == "assistant":
            self._assistant(rec, ts)

    def _user(self, rec: dict, ts: float | None) -> None:
        content = (rec.get("message") or {}).get("content")
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        prompt = False
        for c in content or []:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "tool_result":
                name = self.pending.pop(c.get("tool_use_id"), None)
                if name in WAIT_TOOLS and self.state == WAITING and not self._waiting_tool():
                    self._set(RUNNING, ts)          # 답을 받아 이어서 일한다
            elif c.get("type") == "text" and not rec.get("isMeta"):
                if str(c.get("text", "")).startswith("[Request interrupted"):
                    self.pending.clear()
                    self.blocked = False
                    self._set(None, ts)
                    return
                prompt = True
        if prompt:
            self.blocked = False
            self.turn_at = ts
            self._set(RUNNING, ts)

    def _assistant(self, rec: dict, ts: float | None) -> None:
        if rec.get("isApiErrorMessage"):
            self._set(LIMITED if rec.get("error") == "rate_limit" else None, ts)
            return
        msg = rec.get("message") or {}
        for c in msg.get("content") or []:
            if not isinstance(c, dict) or c.get("type") != "tool_use":
                continue
            name = c.get("name")
            self.pending[c.get("id")] = name
            if name in WAIT_TOOLS:
                self._set(WAITING, ts)
            elif name == WRAP_UP and (c.get("input") or {}).get("blocked"):
                self.blocked = True
            elif self.state != WAITING:
                self._set(RUNNING, ts)
        if msg.get("stop_reason") == "end_turn":
            self.pending = {k: v for k, v in self.pending.items() if v in WAIT_TOOLS}
            self._set(WAITING if self.blocked or self.pending else None, ts)

    def _waiting_tool(self) -> bool:
        return any(n in WAIT_TOOLS for n in self.pending.values())


def _desktop_index() -> dict[str, dict]:
    """대화 기록 ID → 데스크톱 앱 세션 정보."""
    out = {}
    for path in glob.glob(os.path.join(DESKTOP_META, "**", "local_*.json"), recursive=True):
        meta = read_json(path)
        if meta.get("cliSessionId"):
            out[meta["cliSessionId"]] = meta
    return out


def collect(since: float, now: float, cache: JsonlCache) -> list[Item]:
    desktop = None
    items = []
    for path in glob.glob(os.path.join(PROJECTS, "*", "*.jsonl")):
        if mtime(path) < since:
            continue
        log = cache.get(path, ClaudeLog, last=TAIL_BYTES)
        if log is None:
            continue
        if desktop is None:
            desktop = _desktop_index()
        sid = os.path.basename(path)[:-6]
        meta = desktop.get(sid, {})
        if meta.get("isArchived"):
            continue
        state, began, updated = log.state, log.since, log.updated
        if meta.get("interruptedAwaitingPerson") and state != LIMITED:
            state, began = WAITING, began if state == WAITING else None
            updated = max(updated or 0, epoch(meta.get("lastActivityAt")) or 0) or None
        if state == RUNNING:          # 서브에이전트에게 맡기고 기다리는 동안은 서브에이전트 기록이 쌓인다
            subs = glob.glob(os.path.join(path[:-6], "subagents", "agent-*.jsonl"))
            updated = max([updated or 0] + [mtime(p) for p in subs]) or None
        status = settle(state, updated, now)
        if status is None:
            continue
        items.append(Item(
            uid=f"claude:{sid}", agent="claude", label="Claude",
            title=meta.get("title") or log.custom_title or short(log.last_prompt)
            or os.path.basename(log.cwd or "") or sid[:8],
            status=status, since=began, updated_at=updated,
            open_url=f"claude://code/continue?session={meta['sessionId']}" if meta.get("sessionId") else None))
    return items
