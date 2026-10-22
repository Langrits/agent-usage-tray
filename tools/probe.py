"""개발용: 각 서비스 사용량 주소의 응답 구조를 확인한다.

이슈에 붙여도 되도록 가린다: ID·이름·이메일·토큰·결제 정보 같은 항목은 값을 지우고,
긴 문자열은 길이만, 그 밖의 문자열 속 이메일·토큰처럼 보이는 부분도 지운다.
그래도 붙이기 전에 한 번 읽어 보세요.
사용법: python tools/probe.py cursor|codex|claude
"""
import base64
import json
import os
import re
import sqlite3
import sys

import requests

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
TOKENISH = re.compile(r"[A-Za-z0-9_\-.]{24,}")          # 토큰·키·긴 ID처럼 보이는 덩어리
SECRET_KEY = re.compile(r"(^|_)(id|ids|email|name|user|account|token|secret|key|payment|customer|org|uuid|sub)($|_)", re.I)


def redact(o, key: str = ""):
    if isinstance(o, dict):
        return {k: redact(v, k) for k, v in o.items()}
    if isinstance(o, list):
        return [redact(v, key) for v in o]
    if isinstance(o, str):
        if SECRET_KEY.search(re.sub(r"([a-z])([A-Z])", r"\1_\2", key)):
            return "<redacted>"
        if len(o) > 60:
            return f"<str {len(o)}>"
        return TOKENISH.sub("<redacted>", EMAIL.sub("<email>", o))
    return o


def show(name, resp):
    print(f"== {name}: HTTP {resp.status_code}")
    try:
        print(json.dumps(redact(resp.json()), ensure_ascii=False, indent=1)[:4000])
    except ValueError:      # JSON이 아닌 응답(오류 페이지 등)도 가린 뒤 앞부분만
        print(TOKENISH.sub("<redacted>", EMAIL.sub("<email>", resp.text[:300])))


def jwt_claims(tok):
    part = tok.split(".")[1]
    part += "=" * (-len(part) % 4)
    return json.loads(base64.urlsafe_b64decode(part))


def probe_cursor():
    db = os.path.join(os.environ["APPDATA"], "Cursor", "User", "globalStorage", "state.vscdb")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    tok = con.execute("select value from ItemTable where key='cursorAuth/accessToken'").fetchone()[0]
    user_id = jwt_claims(tok)["sub"].split("|")[-1]
    cookie = {"WorkosCursorSessionToken": f"{user_id}%3A%3A{tok}"}
    show("cursor.com/api/usage-summary",
         requests.get("https://cursor.com/api/usage-summary", cookies=cookie, timeout=15))
    show("api2 full_stripe_profile",
         requests.get("https://api2.cursor.sh/auth/full_stripe_profile",
                      headers={"Authorization": f"Bearer {tok}"}, timeout=15))


def probe_codex():
    auth = json.load(open(os.path.expanduser("~/.codex/auth.json"), encoding="utf-8"))["tokens"]
    show("chatgpt.com/backend-api/wham/usage",
         requests.get("https://chatgpt.com/backend-api/wham/usage",
                      headers={"Authorization": f"Bearer {auth['access_token']}",
                               "ChatGPT-Account-Id": auth["account_id"],
                               "User-Agent": "codex_cli_rs"}, timeout=15))


def probe_claude():
    oauth = json.load(open(os.path.expanduser("~/.claude/.credentials.json"), encoding="utf-8"))["claudeAiOauth"]
    show("api.anthropic.com/api/oauth/usage",
         requests.get("https://api.anthropic.com/api/oauth/usage",
                      headers={"Authorization": f"Bearer {oauth['accessToken']}",
                               "anthropic-beta": "oauth-2025-04-20",
                               "User-Agent": "agent-usage-tray"}, timeout=15))


if __name__ == "__main__":
    {"cursor": probe_cursor, "codex": probe_codex, "claude": probe_claude}[sys.argv[1]]()
