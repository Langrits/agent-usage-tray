"""작업 현황(진행 중인 작업만) 수집 테스트. 가짜 기록만 쓰고 실제 로그·네트워크는 안 쓴다.

실행: .venv\\Scripts\\python.exe -m unittest discover tests
"""
import json
import os
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timezone
from unittest import mock

from agent_usage import i18n
from agent_usage.activity import claude as claude_src
from agent_usage.activity import codex as codex_src
from agent_usage.activity import cursor as cursor_src
from agent_usage.activity._files import JsonlCache
from agent_usage.activity.claude import ClaudeLog
from agent_usage.activity.codex import CodexLog
from agent_usage.activity.model import GONE_S, KEEP_S, LIMITED, RUNNING, STALL_S, STALLED, WAITING, Item, settle
from agent_usage.activity_panel import duration, elide, limit_note, status_line
from agent_usage.models import Window

NL = "\n"


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def user(ts, text="hi"):
    return {"type": "user", "timestamp": iso(ts), "sessionId": "s1", "cwd": "C:/work/proj",
            "message": {"role": "user", "content": text}}


def tool_use(ts, name, tid="t1", stop="tool_use", **inp):
    return {"type": "assistant", "timestamp": iso(ts),
            "message": {"role": "assistant", "stop_reason": stop,
                        "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}}


def tool_result(ts, tid="t1"):
    return {"type": "user", "timestamp": iso(ts),
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid}]}}


def end_turn(ts):
    return {"type": "assistant", "timestamp": iso(ts),
            "message": {"role": "assistant", "stop_reason": "end_turn", "content": [{"type": "text", "text": "done"}]}}


def feed(log, *records):
    for r in records:
        log.feed(r)
    return log


def write_jsonl(path, records):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        for r in records:
            f.write(json.dumps(r) + NL)


class ClaudeStateTest(unittest.TestCase):
    def setUp(self):
        self.now = time.time()

    def test_running_from_the_prompt(self):
        log = feed(ClaudeLog(), user(self.now - 60), tool_use(self.now - 50, "Bash", command="ls"))
        self.assertEqual((log.state, round(log.since)), (RUNNING, round(self.now - 60)))

    def test_finished_turn_is_not_shown(self):
        log = feed(ClaudeLog(), user(self.now - 60), tool_use(self.now - 50, "Bash", command="ls"),
                   tool_result(self.now - 40), end_turn(self.now - 30))
        self.assertIsNone(log.state)

    def test_question_waits_then_runs_again_after_the_answer(self):
        log = feed(ClaudeLog(), user(self.now - 60), tool_use(self.now - 50, "AskUserQuestion", questions=[]))
        self.assertEqual((log.state, round(log.since)), (WAITING, round(self.now - 50)))
        log.feed(tool_result(self.now - 20))
        self.assertEqual((log.state, round(log.since)), (RUNNING, round(self.now - 60)))   # 턴 시작부터

    def test_blocked_wrap_up_waits_until_the_next_prompt(self):
        log = feed(ClaudeLog(), user(self.now - 60), tool_use(self.now - 50, "mcp__ccd_turn__wrap_up", blocked=True),
                   tool_result(self.now - 49), end_turn(self.now - 48))
        self.assertEqual(log.state, WAITING)
        log.feed(user(self.now - 10, "next"))
        self.assertEqual(log.state, RUNNING)

    def test_interrupt_ends_the_turn(self):
        log = feed(ClaudeLog(), user(self.now - 60), tool_use(self.now - 50, "Bash", command="ls"),
                   user(self.now - 40, "[Request interrupted by user]"))
        self.assertIsNone(log.state)

    def test_rate_limit_error_stops_it(self):
        log = feed(ClaudeLog(), user(self.now - 60),
                   {"type": "assistant", "timestamp": iso(self.now - 50), "isApiErrorMessage": True,
                    "error": "rate_limit", "message": {"role": "assistant", "content": []}})
        self.assertEqual((log.state, round(log.since)), (LIMITED, round(self.now - 50)))

    def test_turn_start_unknown_when_only_the_end_was_read(self):
        log = feed(ClaudeLog(), tool_use(self.now - 50, "Bash", command="ls"))
        self.assertEqual((log.state, log.since), (RUNNING, None))


class SettleTest(unittest.TestCase):
    def test_running_becomes_stalled_then_disappears(self):
        now = 1_000_000.0
        self.assertEqual(settle(RUNNING, now - STALL_S + 1, now), RUNNING)
        self.assertEqual(settle(RUNNING, now - STALL_S - 1, now), STALLED)
        self.assertIsNone(settle(RUNNING, now - GONE_S - 1, now))

    def test_waiting_and_limited_last_a_day(self):
        now = 1_000_000.0
        self.assertEqual(settle(WAITING, now - GONE_S - 1, now), WAITING)
        self.assertEqual(settle(LIMITED, now - KEEP_S + 1, now), LIMITED)
        self.assertIsNone(settle(LIMITED, now - KEEP_S - 1, now))
        self.assertIsNone(settle(None, now, now))


class CodexStateTest(unittest.TestCase):
    def meta(self, ts, **extra):
        return {"type": "session_meta", "timestamp": iso(ts),
                "payload": {"id": "c1", "cwd": "C:/work", "originator": "codex_exec", "timestamp": iso(ts), **extra}}

    def event(self, ts, kind, **payload):
        return {"type": "event_msg", "timestamp": iso(ts), "payload": {"type": kind, **payload}}

    def test_turns_approval_and_limit(self):
        now = time.time()
        log = feed(CodexLog(), self.meta(now - 100),
                   {"type": "response_item", "timestamp": iso(now - 99),
                    "payload": {"type": "message", "role": "user",
                                "content": [{"type": "input_text", "text": "<environment_context>x"},
                                            {"type": "input_text", "text": "Review the repo"}]}})
        self.assertEqual((log.first_prompt, log.state), ("Review the repo", None))
        log.feed(self.event(now - 90, "task_started"))
        self.assertEqual((log.state, round(log.since)), (RUNNING, round(now - 90)))
        log.feed(self.event(now - 80, "exec_approval_request"))
        self.assertEqual((log.state, round(log.since)), (WAITING, round(now - 80)))
        log.feed(self.event(now - 75, "exec_command_begin"))
        self.assertEqual(log.state, RUNNING)
        log.feed(self.event(now - 70, "token_count", rate_limits={"rate_limit_reached_type": "primary"}))
        log.feed(self.event(now - 60, "task_complete"))
        self.assertEqual((log.state, round(log.since)), (LIMITED, round(now - 70)))

    def test_gap_forgets_when_the_turn_started(self):
        now = time.time()
        log = feed(CodexLog(), self.meta(now - 100), self.event(now - 90, "task_started"))
        log.gap()
        self.assertEqual((log.state, log.since), (RUNNING, None))

    def test_collect_skips_subagents_and_finished_runs(self):
        now = time.time()
        root = tempfile.mkdtemp()
        day = os.path.join(root, "sessions", "2026", "10", "30")
        write_jsonl(os.path.join(day, "rollout-a.jsonl"), [self.meta(now - 60), self.event(now - 50, "task_started")])
        write_jsonl(os.path.join(day, "rollout-b.jsonl"),
                    [self.meta(now - 60, id="c2", source={"subagent": {"thread_id": "c1"}}),
                     self.event(now - 50, "task_started")])
        write_jsonl(os.path.join(day, "rollout-c.jsonl"),
                    [self.meta(now - 60, id="c3"), self.event(now - 50, "task_started"),
                     self.event(now - 40, "task_complete")])
        with mock.patch.object(codex_src, "CODEX_DIR", root):
            items = codex_src.collect(now - KEEP_S, now, JsonlCache())
        self.assertEqual([(i.uid, i.label, i.status) for i in items], [("codex:c1", "Codex exec", RUNNING)])


class ClaudeCollectTest(unittest.TestCase):
    def setUp(self):
        self.now = time.time()
        self.root = tempfile.mkdtemp()
        self.projects = os.path.join(self.root, "projects")
        self.meta_dir = os.path.join(self.root, "desktop")
        os.makedirs(self.meta_dir)

    def session(self, sid, records, meta=None):
        write_jsonl(os.path.join(self.projects, "proj", f"{sid}.jsonl"), records)
        if meta is not None:
            with open(os.path.join(self.meta_dir, f"local_{sid}.json"), "w", encoding="utf-8") as f:
                json.dump({"cliSessionId": sid, "sessionId": f"local_{sid}", **meta}, f)

    def collect(self):
        with mock.patch.object(claude_src, "PROJECTS", self.projects), \
                mock.patch.object(claude_src, "DESKTOP_META", self.meta_dir):
            return {i.uid: i for i in claude_src.collect(self.now - KEEP_S, self.now, JsonlCache())}

    def test_desktop_title_link_and_archived(self):
        self.session("a", [user(self.now - 60), tool_use(self.now - 50, "Bash", command="ls")], {"title": "결제 리팩터링"})
        self.session("b", [user(self.now - 60), tool_use(self.now - 50, "Bash", command="ls")], {"isArchived": True})
        items = self.collect()
        self.assertEqual(list(items), ["claude:a"])
        self.assertEqual(items["claude:a"].title, "결제 리팩터링")
        self.assertEqual(items["claude:a"].open_url, "claude://code/continue?session=local_a")

    def test_subagent_log_keeps_a_quiet_parent_running(self):
        old = self.now - STALL_S - 120
        self.session("a", [user(old - 10), tool_use(old, "Task", description="explore")])
        sub = os.path.join(self.projects, "proj", "a", "subagents", "agent-x.jsonl")
        write_jsonl(sub, [user(self.now - 30)])
        self.assertEqual(self.collect()["claude:a"].status, RUNNING)
        os.utime(sub, (old, old))
        self.assertEqual(self.collect()["claude:a"].status, STALLED)

    def test_desktop_says_it_waits_for_the_person(self):
        self.session("a", [user(self.now - 60), end_turn(self.now - 50)], {"interruptedAwaitingPerson": True})
        item = self.collect()["claude:a"]
        self.assertEqual((item.status, item.since), (WAITING, None))


class CursorCollectTest(unittest.TestCase):
    def test_only_explicit_states(self):
        now = time.time()
        db = os.path.join(tempfile.mkdtemp(), "state.vscdb")
        con = sqlite3.connect(db)
        con.execute("create table composerHeaders (composerId, lastUpdatedAt, createdAt, isArchived, isSubagent, value)")
        con.execute("create table cursorDiskKV (key, value)")
        ms = int(now * 1000) - 5000
        rows = [("gen", {"name": "작업 중인 대화"}, "generating", 0, 0),
                ("plan", {"name": "계획 승인 대기", "hasPendingPlan": True}, None, 0, 0),
                ("done", {"name": "끝난 대화"}, "completed", 0, 0),
                ("nostate", {"name": "상태 없음"}, None, 0, 0),
                ("arch", {"name": "보관"}, "generating", 1, 0),
                ("sub", {"name": "서브"}, "generating", 0, 1)]
        for cid, head, status, archived, sub in rows:
            head["workspaceIdentifier"] = {"uri": "file:///c%3A/work/app"}
            con.execute("insert into composerHeaders values (?,?,?,?,?,?)",
                        (cid, ms, ms, archived, sub, json.dumps(head)))
            if status:
                con.execute("insert into cursorDiskKV values (?,?)", (f"composerData:{cid}", json.dumps({"status": status})))
        con.commit()
        con.close()
        with mock.patch.object(cursor_src, "STATE_DB", db):
            items = {i.uid: i for i in cursor_src.collect(now - KEEP_S, now)}
        self.assertEqual({k: v.status for k, v in items.items()}, {"cursor:gen": RUNNING, "cursor:plan": WAITING})
        self.assertTrue(items["cursor:gen"].open_url.startswith("cursor://file/"))


class Counter:
    def __init__(self):
        self.ids = []
        self.gaps = 0

    def feed(self, rec):
        self.ids.append(rec["i"])

    def gap(self):
        self.gaps += 1


class JsonlCacheTest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "log.jsonl")

    def write(self, text, mode="w"):
        with open(self.path, mode, encoding="utf-8", newline="") as f:
            f.write(text)

    def test_partial_line_waits_until_complete(self):
        cache = JsonlCache()
        self.write('{"i": 1}' + NL + '{"i": 2}' + NL + '{"i"')
        self.assertEqual(cache.get(self.path, Counter).ids, [1, 2])
        self.write(': 3}' + NL, "a")
        self.assertEqual(cache.get(self.path, Counter).ids, [1, 2, 3])

    def test_rewritten_file_is_read_again(self):
        cache = JsonlCache()
        self.write('{"i": 1}' + NL + '{"i": 2}' + NL)
        cache.get(self.path, Counter)
        self.write('{"i": 7}' + NL + '{"i": 8}' + NL + '{"i": 9}' + NL)   # 같은 이름, 다른 내용, 더 큼
        self.assertEqual(cache.get(self.path, Counter).ids, [7, 8, 9])

    def test_big_file_reads_only_its_start_and_end(self):
        self.write("".join('{"i": %d}' % i + NL for i in range(100)))     # 줄마다 9~10바이트
        cache = JsonlCache()
        c = cache.get(self.path, Counter, first=20, last=30)
        self.assertEqual((c.ids, c.gaps), ([0, 1, 97, 98, 99], 1))
        self.write('{"i": 100}' + NL, "a")                                 # 이어 붙은 줄은 그대로 읽는다
        self.assertEqual(cache.get(self.path, Counter, first=20, last=30).ids, [0, 1, 97, 98, 99, 100])

    def test_prune_drops_unused_files(self):
        cache = JsonlCache()
        self.write('{"i": 1}' + NL)
        cache.get(self.path, Counter)
        cache.prune()
        self.assertEqual(len(cache), 1)
        cache.prune()                                      # 이번 수집에서 안 씀
        self.assertEqual(len(cache), 0)


class PanelTextTest(unittest.TestCase):
    def setUp(self):
        i18n.set_language("ko")

    def tearDown(self):
        i18n.set_language("auto")

    def test_elide_fits_the_width(self):
        measure = lambda s: len(s) * 7
        self.assertEqual(elide("짧은 제목", 100, measure), "짧은 제목")
        out = elide("아주 긴 대화 제목이 패널 폭을 넘어간다", 70, measure)
        self.assertTrue(out.endswith("…") and measure(out) <= 70)

    def test_duration(self):
        self.assertEqual(duration(30), "1분")
        self.assertEqual(duration(12 * 60 + 5), "12분")
        self.assertEqual(duration(2 * 3600), "2시간")
        self.assertEqual(duration(3600 + 5 * 60), "1시간 5분")

    def test_limit_note_uses_the_last_reset_of_spent_limits(self):
        now = time.time()
        spent = [Window("five_hour", "5h", 100, now + 3600), Window("seven_day", "W", 100, now + 7200)]
        from agent_usage import format as fmt
        self.assertEqual(limit_note(spent), i18n.t("a_resets", clock=fmt.clock(now + 7200)))
        self.assertEqual(limit_note([Window("five_hour", "5h", 40, now + 3600)]), "한도 초기화됨")
        self.assertEqual(limit_note(None), "")

    def test_status_line(self):
        now = time.time()
        run = Item("claude:a", "claude", "Claude", "t", RUNNING, now - 12 * 60, now)
        self.assertEqual(status_line(run, now), "Claude · 작업 중 · 12분")
        unknown = Item("claude:b", "claude", "Claude", "t", RUNNING, None, now)
        self.assertEqual(status_line(unknown, now), "Claude · 작업 중")
        stalled = Item("codex:c", "codex", "Codex", "t", STALLED, now - 3600, now - 15 * 60)
        self.assertEqual(status_line(stalled, now), "Codex · 15분째 기록 없음")


class StringsTest(unittest.TestCase):
    def test_korean_and_english_have_same_keys(self):
        self.assertEqual(set(i18n.KO), set(i18n.EN))
