"""Claude (claude.ai Pro/Max 구독).

Claude Code CLI 로그인(~/.claude/.credentials.json)으로 Claude Code의 /usage와 같은 주소를 조회한다.
claude.ai 웹 주소는 Cloudflare가 프로그램 접속을 막아서 쓰지 않는다.
접속 토큰은 8시간 정도면 만료된다. 자동 갱신이 켜져 있으면 Claude Code와 같은 방식으로 갱신하고
새 토큰을 같은 파일에 다시 써서 Claude Code CLI도 계속 쓸 수 있게 한다.
"""
from __future__ import annotations

import logging
import os
import time

import requests

from ..i18n import t
from ..models import ResetCredits, Usage, Window
from ._util import USER_AGENT, HOME, LoginProblem, Options, epoch, read_json, write_json_atomic

log = logging.getLogger("agent_usage")

ID = "claude"
TITLE = "Claude"
EXPERIMENTAL = False

CLAUDE_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(HOME, ".claude")
CRED_PATH = os.path.join(CLAUDE_DIR, ".credentials.json")
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"   # Claude Code CLI (공개 클라이언트)

ORDER = ["five_hour", "seven_day"]


def detect() -> bool:
    return os.path.exists(CRED_PATH)


def _access_token(opts: Options) -> tuple[str, dict]:
    try:
        creds = read_json(CRED_PATH)
    except FileNotFoundError:
        raise LoginProblem(t("err_no_login", title="Claude Code"), t("hint_claude_login"))
    oauth = creds.get("claudeAiOauth") or {}
    if not oauth.get("accessToken"):
        raise LoginProblem(t("err_no_login", title="Claude Code"), t("hint_claude_login"))
    if (oauth.get("expiresAt") or 0) / 1000 > time.time() + 120:
        return oauth["accessToken"], oauth
    if not opts.auto_refresh_login:
        raise LoginProblem(t("err_expired", title="Claude"), t("hint_claude_run"))
    if not oauth.get("refreshToken"):
        raise LoginProblem(t("err_expired", title="Claude"), t("hint_claude_login"))

    resp = requests.post(TOKEN_URL, timeout=15, json={
        "grant_type": "refresh_token",
        "refresh_token": oauth["refreshToken"],
        "client_id": CLIENT_ID,
    })
    if resp.status_code in (400, 401, 403):
        raise LoginProblem(t("err_expired", title="Claude"), t("hint_claude_login"))
    resp.raise_for_status()
    tok = resp.json()

    # 갱신 직전에 CLI가 먼저 갱신했을 수 있으니 파일을 다시 읽어 그 위에 덮어쓴다
    creds = read_json(CRED_PATH)
    oauth = creds.setdefault("claudeAiOauth", {})
    oauth["accessToken"] = tok["access_token"]
    if tok.get("refresh_token"):
        oauth["refreshToken"] = tok["refresh_token"]
    oauth["expiresAt"] = int((time.time() + int(tok.get("expires_in") or 28800)) * 1000)
    if tok.get("refresh_token_expires_in"):
        oauth["refreshTokenExpiresAt"] = int((time.time() + int(tok["refresh_token_expires_in"])) * 1000)
    if tok.get("scope"):
        oauth["scopes"] = tok["scope"].split()
    write_json_atomic(CRED_PATH, creds)
    log.info("claude: login renewed (expires %s)", time.strftime("%m-%d %H:%M", time.localtime(oauth["expiresAt"] / 1000)))
    return oauth["accessToken"], oauth


def _plan(oauth: dict) -> str | None:
    sub = (oauth.get("subscriptionType") or "").lower()
    tier = (oauth.get("rateLimitTier") or "").lower()
    if sub == "max":
        for mult in ("20x", "5x"):
            if mult in tier:
                return f"Max {mult}"
        return "Max"
    return sub.capitalize() or None


def fetch(opts: Options) -> Usage:
    usage = Usage(ID, TITLE, source=t("source_api"))
    try:
        token, oauth = _access_token(opts)
    except LoginProblem as p:
        return usage.fail(p.error, p.hint)

    usage.plan = _plan(oauth)
    resp = requests.get(USAGE_URL, timeout=15, headers={
        "Authorization": f"Bearer {token}",
        "anthropic-beta": "oauth-2025-04-20",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    })
    if resp.status_code == 401:
        return usage.fail(t("err_expired", title="Claude"), t("hint_claude_login"))
    if resp.status_code == 429:
        return usage.fail(t("err_rate_limited"), t("hint_rate_limited"))
    resp.raise_for_status()
    parse(usage, resp.json())
    return usage


def parse(usage: Usage, d: dict) -> None:
    if isinstance(d.get("limits"), list) and d["limits"]:
        _parse_limits(usage, d["limits"])
    else:
        _parse_legacy(usage, d)

    extra = d.get("extra_usage") or {}
    if extra.get("is_enabled") and extra.get("used_credits") is not None:
        unit = 10 ** (extra.get("decimal_places") or 2)
        used, limit = extra["used_credits"] / unit, extra.get("monthly_limit")
        usage.extra.append(t("extra_usage_limit", used=f"{used:.2f}", limit=f"{limit / unit:.0f}") if limit
                           else t("extra_usage", used=f"{used:.2f}"))

    rows = [r for r in (d.get("seven_day_breakdown") or {}).get("rows", []) if r.get("percent")]
    if rows:
        usage.extra.append(t("week_breakdown",
                             rows=" · ".join(f"{r['display_name']} {r['percent']:.0f}%" for r in rows)))

    rc = _find_reset_credits(d)
    if rc:
        usage.reset_credits = rc


def _parse_limits(usage: Usage, limits: list) -> None:
    """2026년 응답의 limits 목록: session / weekly_all / weekly_scoped(모델별)."""
    for lim in limits:
        if lim.get("percent") is None:
            continue
        kind, group = lim.get("kind"), lim.get("group")
        if kind == "session":
            key, label = "five_hour", t("w_5h")
        elif kind == "weekly_all":
            key, label = "seven_day", t("w_week")
        else:
            scope = lim.get("scope") or {}
            name = ((scope.get("model") or {}).get("display_name")
                    or (scope.get("surface") or {}).get("display_name") or kind)
            if group == "weekly":
                key, label = f"seven_day_{name}".lower(), t("w_week_scoped", name=name)
            else:
                key, label = f"{kind}_{name}".lower(), str(name)
        usage.windows.append(Window(key, label, float(lim["percent"]), epoch(lim.get("resets_at"))))


def _parse_legacy(usage: Usage, d: dict) -> None:
    """limits 목록이 없던 예전 응답: five_hour / seven_day / seven_day_<모델> 객체."""
    for key, val in d.items():
        if key == "extra_usage" or not isinstance(val, dict) or val.get("utilization") is None:
            continue
        if key == "five_hour":
            label = t("w_5h")
        elif key == "seven_day":
            label = t("w_week")
        elif key == "seven_day_oauth_apps":
            label = t("w_week_ext")
        elif key.startswith("seven_day_"):
            label = t("w_week_scoped", name=key.removeprefix("seven_day_").title())
        else:
            label = key
        usage.windows.append(Window(key, label, float(val["utilization"]), epoch(val.get("resets_at"))))
    usage.windows.sort(key=lambda w: ORDER.index(w.key) if w.key in ORDER else len(ORDER))


def _find_reset_credits(d: dict) -> ResetCredits | None:
    """재설정권. 2026-10 기준 이 주소의 응답에는 없다. 생기면 잡히도록 이름에 reset이 들어간 개수 항목을 찾는다."""
    for key, val in d.items():
        k = key.lower()
        if "reset" not in k or k.endswith("resets_at"):
            continue
        if isinstance(val, dict):
            count = val.get("available_count", val.get("count", val.get("available")))
            if isinstance(count, (int, float)) and not isinstance(count, bool):
                exp = next((epoch(v) for kk, v in val.items() if "expire" in kk.lower() and v), None)
                return ResetCredits(int(count), exp)
        if isinstance(val, list):
            exps = [e for e in (epoch(x.get("expires_at")) for x in val if isinstance(x, dict)) if e]
            return ResetCredits(len(val), min(exps) if exps else None)
        if isinstance(val, int) and not isinstance(val, bool):
            return ResetCredits(val)
    return None
