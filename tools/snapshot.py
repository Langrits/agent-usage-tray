"""개발용: 앱을 띄워 팝업·그래프·설정 화면을 PNG로 저장하고 종료한다.

python tools/snapshot.py <저장폴더> [--fake] [--lang ko|en] [--dark]
--fake: 실제 조회 대신 가짜 데이터(4개 서비스)와 가짜 7일치 기록을 임시 폴더에 넣는다 (README 스크린샷용).
"""
import os
import random
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
out_dir = os.path.abspath(sys.argv[1])
args = sys.argv[2:]
fake = "--fake" in args
lang = args[args.index("--lang") + 1] if "--lang" in args else None
suffix = (f"_{lang}" if lang else "") + ("_dark" if "--dark" in args else "")
if fake:
    os.environ["AGENT_USAGE_DATA"] = tempfile.mkdtemp(prefix="agent_usage_demo_")

from PIL import ImageGrab  # noqa: E402

import agent_usage.app as app_mod  # noqa: E402
from agent_usage import theme  # noqa: E402
from agent_usage.i18n import t  # noqa: E402
from agent_usage.models import ResetCredits, Usage, Window  # noqa: E402
from agent_usage.store import settings  # noqa: E402

if "--dark" in args:
    theme.is_light = lambda: False
if lang:
    settings()["language"] = lang


def fake_usages(ids, opts):
    now = time.time()
    return {
        "claude": Usage("claude", "Claude", "Max 5x", [
            Window("five_hour", t("w_5h"), 18, now + 3 * 3600 + 120),
            Window("seven_day", t("w_week"), 27, now + 4.9 * 86400),
            Window("seven_day_fable", t("w_week_scoped", name="Fable"), 0, now + 4.9 * 86400)],
            extra=[t("week_breakdown", rows="Claude Code 100%")], source="API"),
        "codex": Usage("codex", "Codex", "Pro", [Window("weekly", t("w_week"), 14, now + 6 * 86400)],
                       reset_credits=ResetCredits(1, now + 12 * 86400), source="API"),
        "cursor": Usage("cursor", "Cursor", "Ultra",
                        [Window("month", t("w_month"), 30, now + 12 * 86400, "Auto 32% · API 13%")], source="API"),
        "gemini": Usage("gemini", "Gemini", "Gemini Code Assist", [
            Window("daily_gemini-2.5-pro", t("w_daily_model", model="2.5 Pro"), 62, now + 9 * 3600),
            Window("daily_gemini-2.5-flash", t("w_daily_model", model="2.5 Flash"), 4, now + 9 * 3600)],
            extra=[t("gemini_note")], source="API"),
    }


def seed(history):
    now = time.time()
    rnd = random.Random(1)
    series = [("claude", "five_hour", 5 * 3600, 2.2), ("claude", "seven_day", 7 * 86400, 0.12),
              ("codex", "weekly", 7 * 86400, 0.15), ("cursor", "month", 30 * 86400, 0.06)]
    rows = []
    for provider, key, period, rate in series:
        used, phase = 0.0, rnd.random() * period
        for i in range(7 * 24 * 12):
            ts = now - 7 * 86400 + i * 300
            if (ts + phase) % period < 300:
                used = 0.0
            if 9 <= time.localtime(ts).tm_hour <= 19:
                used = min(100.0, used + rnd.random() * rate)
            label = {"five_hour": t("w_5h"), "month": t("w_month")}.get(key, t("w_week"))
            rows.append((ts, provider, key, label, used, None))
    history.con.executemany("insert into samples values (?,?,?,?,?,?)", rows)
    history.con.commit()


if fake:
    app_mod.fetch_all = fake_usages
    app_mod.App.compute_visible = lambda self: ["claude", "codex", "cursor", "gemini"]

app = app_mod.App()
app.notifier.check = lambda usage: None     # 캡처 중에는 실제 알림을 보내지 않는다
if fake:
    seed(app.history)


def grab(win, name):
    win.update()
    x, y, w, h = win.winfo_rootx(), win.winfo_rooty(), win.winfo_width(), win.winfo_height()
    ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True).save(os.path.join(out_dir, name))
    print("saved", name, w, h)


def wait_fetch():
    if app.fetching or not app.latest:
        app.root.after(300, wait_fetch)
        return
    app.popup.show()
    app.root.after(600, check_rerender)


def check_rerender():
    body = app.popup.body
    app.popup.render()                      # 20초 갱신과 같은 경로
    print("rebuilt on re-render:", app.popup.body is not body)
    grab(app.popup.win, f"popup{suffix}.png")
    if not app.settings["history_open"]:
        app.popup.toggle_history()
    app.root.after(1200, shot_history)


def shot_history():
    """팝업 옆 사용 기록 패널: 두 창을 한 장으로 (첫 구역 그래프에 마우스를 올린 상태)."""
    panel = app.popup.history
    first = next(iter(panel.sections.values()), None)
    if first is not None:
        first.event_generate("<Motion>", x=int(first.winfo_width() * 0.7), y=int(first.winfo_height() * 0.5))
    wins = [panel.win, app.popup.win]
    for w in wins:
        w.update()
    x0 = min(w.winfo_rootx() for w in wins)
    y0 = min(w.winfo_rooty() for w in wins)
    x1 = max(w.winfo_rootx() + w.winfo_width() for w in wins)
    y1 = max(w.winfo_rooty() + w.winfo_height() for w in wins)
    ImageGrab.grab(bbox=(x0, y0, x1, y1), all_screens=True).save(os.path.join(out_dir, f"history{suffix}.png"))
    print("saved", f"history{suffix}.png", x1 - x0, y1 - y0)
    app.open_settings()
    app.root.after(800, shot_settings)


def shot_settings():
    grab(app.settings_win.win, f"settings{suffix}.png")
    app.post("quit")


app.root.after(500, wait_fetch)
app.run()
