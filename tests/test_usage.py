"""사용량 조회·알림·기록 테스트. 실제 설정·로그인 파일과 네트워크는 쓰지 않는다.

실행: .venv/Scripts/python.exe -m unittest discover tests
"""
import importlib.util
import json
import os
import tempfile
import time
import unittest
from unittest import mock

os.environ["AGENT_USAGE_DATA"] = tempfile.mkdtemp(prefix="agent_usage_test_")   # store 를 불러오기 전에

from agent_usage import i18n, winutil  # noqa: E402
from agent_usage.models import Usage, Window  # noqa: E402
from agent_usage.notify import Notifier  # noqa: E402
from agent_usage.providers import BY_ID, Options, claude, codex, cursor, fetch_one, gemini  # noqa: E402
from agent_usage.store import History, legacy_dirs, migrate_legacy, settings  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class LegacyMergeTest(unittest.TestCase):
    """0.1.x가 두 곳(AppData 원래 위치, 스토어 앱 전용 사본)에 나눠 남긴 기록을 새 위치로 합친다."""

    def make_legacy(self, rows, settings_text, mtime):
        import sqlite3
        d = tempfile.mkdtemp()
        con = sqlite3.connect(os.path.join(d, "history.sqlite"))
        con.execute("create table samples (ts real, provider text, window text, label text, used real, resets_at real)")
        con.executemany("insert into samples values (?,?,?,?,?,?)", rows)
        con.commit()
        con.close()
        path = os.path.join(d, "settings.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(settings_text)
        os.utime(path, (mtime, mtime))
        return d

    def count(self, target):
        import sqlite3
        con = sqlite3.connect(os.path.join(target, "history.sqlite"))
        try:
            return con.execute("select count(*) from samples").fetchone()[0]
        finally:
            con.close()

    def test_merges_both_without_duplicates_and_takes_newest_settings(self):
        shared = (1000.0, "claude", "five_hour", "5h", 10.0, None)
        a = self.make_legacy([shared, (1100.0, "claude", "five_hour", "5h", 12.0, None)], '{"pinned": false}', 1_000)
        b = self.make_legacy([shared, (2000.0, "codex", "weekly", "W", 30.0, None)], '{"pinned": true}', 2_000)
        target = tempfile.mkdtemp()
        merged = migrate_legacy(target, [a, b])
        self.assertEqual(self.count(target), 3)
        with open(os.path.join(target, "settings.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"pinned": True})          # 더 최근 것
        migrate_legacy(target, [a, b], merged)                        # 바뀐 게 없으면 다시 합쳐도 그대로
        self.assertEqual(self.count(target), 3)

    def test_legacy_dirs_include_store_app_copies(self):
        local = tempfile.mkdtemp()
        real = os.path.join(local, "AgentUsage")
        boxed = os.path.join(local, "Packages", "Claude_x", "LocalCache", "Local", "AgentUsage")
        os.makedirs(real)
        os.makedirs(boxed)
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": local}):
            self.assertEqual(legacy_dirs(), [real, boxed])


class NotifyTest(unittest.TestCase):
    def test_low_alert_without_reset_time_fires_once(self):
        sent = []
        n = Notifier(settings(), lambda title, msg: sent.append(msg))
        u = Usage("test-noreset", "Test", windows=[Window("w", "W", 90, None)])
        n.check(u)
        n.check(u)
        self.assertEqual(len(sent), 1)


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.h = History()
        self.h.con.execute("delete from samples")
        self.h.con.commit()

    def count(self):
        return self.h.con.execute("select count(*) from samples").fetchone()[0]

    def test_same_sample_is_stored_once(self):
        u = Usage("codex", "Codex", windows=[Window("weekly", "W", 10, None)], data_at=1000.0)
        self.h.add(u)
        self.h.add(u)
        self.assertEqual(self.count(), 1)

    def test_older_log_sample_is_not_duplicated_after_newer_one(self):
        old = Usage("codex", "Codex", windows=[Window("weekly", "W", 10, None)], data_at=1000.0)
        new = Usage("codex", "Codex", windows=[Window("weekly", "W", 12, None)], data_at=2000.0)
        for u in (old, new, old, old):
            self.h.add(u)
        self.assertEqual(self.count(), 2)


class ParseTest(unittest.TestCase):
    def test_codex_missing_percent_gives_no_window(self):
        u = Usage("codex", "Codex")
        codex.parse(u, {"rate_limit": {"primary_window": {"limit_window_seconds": 604800}}})
        self.assertEqual(u.windows, [])

    def test_cursor_missing_numbers_gives_no_window(self):
        u = Usage("cursor", "Cursor")
        cursor.parse(u, {"individualUsage": {"plan": {"enabled": True}}})
        self.assertEqual(u.windows, [])
        u = Usage("cursor", "Cursor")
        cursor.parse(u, {"individualUsage": {"plan": {"enabled": True, "totalPercentUsed": 30.5}}})
        self.assertEqual([w.used_pct for w in u.windows], [30.5])

    def test_odd_numbers_are_not_used_as_percent(self):
        u = Usage("codex", "Codex")
        codex.parse(u, {"rate_limit": {"primary_window": {"used_percent": True, "limit_window_seconds": 604800}}})
        self.assertEqual(u.windows, [])
        u = Usage("cursor", "Cursor")
        cursor.parse(u, {"individualUsage": {"plan": {"enabled": True, "totalPercentUsed": float("inf")}}})
        self.assertEqual(u.windows, [])

    def test_response_without_numbers_becomes_error(self):
        with mock.patch.object(BY_ID["codex"], "fetch", lambda opts: Usage("codex", "Codex")):
            u = fetch_one("codex", Options())
        self.assertFalse(u.ok)
        self.assertEqual(u.error, i18n.t("err_format"))

    def test_cursor_on_demand_only_is_not_an_error(self):
        def on_demand_only(opts):
            u = Usage("cursor", "Cursor")
            cursor.parse(u, {"individualUsage": {"plan": {"enabled": False},
                                                 "onDemand": {"enabled": True, "used": 1234, "limit": 5000}}})
            return u
        with mock.patch.object(BY_ID["cursor"], "fetch", on_demand_only):
            u = fetch_one("cursor", Options())
        self.assertTrue(u.ok)
        self.assertEqual(len(u.extra), 1)


class GeminiEndpointTest(unittest.TestCase):
    def base(self, value):
        env = {"CODE_ASSIST_ENDPOINT": value} if value is not None else {}
        with mock.patch.dict(os.environ, env, clear=False):
            if value is None:
                os.environ.pop("CODE_ASSIST_ENDPOINT", None)
            return gemini._base_url()

    def test_only_https_googleapis_is_allowed(self):
        default = gemini.DEFAULT_ENDPOINT + "/v1internal"
        self.assertEqual(self.base(None), default)
        self.assertEqual(self.base("http://cloudcode-pa.googleapis.com"), default)
        self.assertEqual(self.base("https://example.com"), default)
        self.assertEqual(self.base("https://googleapis.com.evil.io"), default)
        self.assertEqual(self.base("https://staging-cloudcode-pa.googleapis.com/"),
                         "https://staging-cloudcode-pa.googleapis.com/v1internal")

    def test_malformed_endpoint_does_not_crash(self):
        self.assertEqual(self.base("https://[invalid"), gemini.DEFAULT_ENDPOINT + "/v1internal")


class FakeResponse:
    status_code = 200

    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data

    def raise_for_status(self):
        pass


class ClaudeRenewalTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, ".credentials.json")
        self.write("old-access", "old-refresh", time.time() - 10)
        self.patches = [mock.patch.object(claude, "CRED_PATH", self.path),
                        mock.patch.object(claude, "CLAUDE_DIR", self.dir)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def write(self, access, refresh, expires):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"claudeAiOauth": {"accessToken": access, "refreshToken": refresh,
                                         "expiresAt": int(expires * 1000), "subscriptionType": "max"}}, f)

    def read(self):
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)["claudeAiOauth"]

    def test_renews_and_writes_back(self):
        new = {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 28800}
        with mock.patch.object(claude.requests, "post", lambda *a, **k: FakeResponse(new)):
            token, _ = claude._access_token(Options(auto_refresh_login=True))
        self.assertEqual(token, "new-access")
        self.assertEqual(self.read()["refreshToken"], "new-refresh")

    def test_keeps_cli_login_written_during_renewal(self):
        def cli_refreshed_meanwhile(*a, **k):
            self.write("cli-access", "cli-refresh", time.time() + 3600)
            return FakeResponse({"access_token": "app-access", "refresh_token": "app-refresh", "expires_in": 28800})
        with mock.patch.object(claude.requests, "post", cli_refreshed_meanwhile):
            token, _ = claude._access_token(Options(auto_refresh_login=True))
        self.assertEqual(token, "cli-access")
        self.assertEqual(self.read()["refreshToken"], "cli-refresh")   # CLI 것을 덮어쓰지 않음


class ProbeRedactTest(unittest.TestCase):
    def test_ids_names_emails_and_tokens_are_hidden(self):
        spec = importlib.util.spec_from_file_location("probe", os.path.join(ROOT, "tools", "probe.py"))
        probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probe)
        out = probe.redact({"user_id": "user-80abc", "accountId": "6ff7e51b", "email": "a@b.co", "plan_type": "pro",
                            "org_id": 123456789, "used_percent": 14,
                            "note": "mail a@b.co key TESTTOKEN0123456789abcdefghijk"})
        self.assertEqual(out["org_id"], "<redacted>")     # 숫자 ID도 가림
        self.assertEqual(out["used_percent"], 14)
        self.assertEqual(out["user_id"], "<redacted>")
        self.assertEqual(out["accountId"], "<redacted>")
        self.assertEqual(out["email"], "<redacted>")
        self.assertEqual(out["plan_type"], "pro")
        self.assertNotIn("a@b.co", out["note"])
        self.assertNotIn("TESTTOKEN0123456789abcdefghijk", out["note"])


class WindowsTest(unittest.TestCase):
    def test_work_area_at_primary_center_matches_primary_work_area(self):
        left, top, right, bottom = winutil.work_area()
        self.assertEqual(winutil.work_area_at((left + right) // 2, (top + bottom) // 2), (left, top, right, bottom))

    def test_work_area_at_far_away_point_returns_nearest_monitor(self):
        left, top, right, bottom = winutil.work_area_at(10 ** 6, 10 ** 6)
        self.assertLess(left, right)
        self.assertLess(top, bottom)


class StringsTest(unittest.TestCase):
    def test_korean_and_english_have_same_keys(self):
        self.assertEqual(set(i18n.KO), set(i18n.EN))


if __name__ == "__main__":
    unittest.main()
