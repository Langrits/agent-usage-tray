"""개발용: 앱을 띄워 팝업·사용 기록·작업 현황·설정 화면을 PNG로 저장하고 종료한다.

python tools/snapshot.py <저장폴더> [--fake] [--lang ko|en] [--dark]
--fake: 실제 조회 대신 가짜 데이터(4개 서비스), 가짜 7일치 기록, 가짜 작업 현황을 쓴다 (README 스크린샷용).
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

from PIL import Image, ImageGrab  # noqa: E402

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


def fake_activity():
    """README용 가짜 작업 현황 (실제 대화 제목이 공개되지 않게)."""
    from agent_usage.activity import LIMITED, RUNNING, STALLED, WAITING, Item, Snapshot
    ko = (lang or "ko") == "ko"
    now = time.time()
    m = lambda mins: now - mins * 60
    items = [
        Item("codex:1", "codex", "Codex exec", "결제 모듈 변경 검토" if ko else "Review the payment changes",
             WAITING, m(2), m(2)),
        Item("claude:2", "claude", "Claude", "API 문서 정리" if ko else "Tidy up API docs", LIMITED, m(95), m(95),
             open_url="claude://x"),
        Item("claude:1", "claude", "Claude", "결제 모듈 리팩터링" if ko else "Refactor the payment module",
             RUNNING, m(12), m(0), open_url="claude://x"),
        Item("cursor:1", "cursor", "Cursor", "로그인 화면 다듬기" if ko else "Polish the login screen",
             RUNNING, None, m(1), open_url="cursor://x"),
        Item("codex:2", "codex", "Codex", "데이터셋 중복 검사" if ko else "Check dataset duplicates",
             STALLED, m(40), m(14)),
    ]
    return Snapshot(items, [], now)


if fake:
    app_mod.fetch_all = fake_usages
    app_mod.App.compute_visible = lambda self: ["claude", "codex", "cursor", "gemini"]
    app_mod.activity.collect = fake_activity

app = app_mod.App()
app.notifier.check = lambda usage: None     # 캡처 중에는 실제 알림을 보내지 않는다
app.popup._check_focus = lambda: None       # 캡처 중 다른 창을 눌러도 팝업이 닫히지 않게
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
    grab_all([panel.win, app.popup.win], f"history{suffix}.png")
    if not app.settings["activity_open"]:
        app.popup.toggle_activity()
    app.root.after(1200, shot_activity)


def shot_activity():
    """작업 현황 패널까지 연 모습: [작업 현황][사용 기록][팝업]."""
    for c in app.popup.history.sections.values():      # 앞 장면의 마우스 올림 표시는 지운다
        c.delete("hover")
    grab_all([app.popup.activity.win, app.popup.history.win, app.popup.win], f"activity{suffix}.png")
    app.open_settings()
    app.root.after(800, shot_settings)


def grab_all(wins, name):
    """여러 창을 한 장으로. 창 사이·바깥은 투명하게 둔다 (뒤에 있던 다른 프로그램 화면이 찍히지 않게)."""
    for w in wins:
        w.update()
    boxes = [(w.winfo_rootx(), w.winfo_rooty(), w.winfo_rootx() + w.winfo_width(), w.winfo_rooty() + w.winfo_height())
             for w in wins]
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    sheet = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
    for b in boxes:
        sheet.paste(ImageGrab.grab(bbox=b, all_screens=True), (b[0] - x0, b[1] - y0))
    sheet.save(os.path.join(out_dir, name))
    print("saved", name, x1 - x0, y1 - y0)


def shot_settings():
    grab(app.settings_win.win, f"settings{suffix}.png")
    app.post("quit")


app.root.after(500, wait_fetch)
app.run()
