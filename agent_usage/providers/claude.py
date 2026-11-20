"""Claude (claude.ai Pro/Max 구독).

Claude Code CLI 로그인(~/.claude/.credentials.json)으로 Claude Code의 /usage와 같은 주소를 조회한다.
claude.ai 웹 주소는 Cloudflare가 프로그램 접속을 막아서 쓰지 않는다.
접속 토큰은 8시간 정도면 만료된다. 자동 갱신이 켜져 있으면 Claude Code와 같은 방식으로 갱신하고
새 토큰을 같은 파일에 다시 써서 Claude Code CLI도 계속 쓸 수 있게 한다.
재설정권(cedar_ember)은 ?cedar_ember=1을 붙이고 최신 Claude Code로 보이는 요청에만 응답에 들어 있어서,
이 조회는 설치된 Claude Code 버전으로 Claude Code와 같은 사용자 에이전트를 보낸다.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import time

import requests

from ..i18n import t
from ..models import ResetCredits, Usage, Window
from ._util import HOME, LoginProblem, Options, as_number, epoch, read_json, write_json_atomic

log = logging.getLogger("agent_usage")

ID = "claude"
TITLE = "Claude"
EXPERIMENTAL = False

CLAUDE_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(HOME, ".claude")
CRED_PATH = os.path.join(CLAUDE_DIR, ".credentials.json")
USAGE_URL = "https://api.anthropic.com/api/oauth/usage?cedar_ember=1"
TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"   # Claude Code CLI (공개 클라이언트)
CLI_VERSION_FALLBACK = "2.1.326"   # 설치된 Claude Code를 못 찾을 때 (너무 오래된 버전이면 재설정권을 주지 않는다)
_VERSION = re.compile(r"\d+\.\d+\.\d+")

ORDER = ["five_hour", "seven_day"]


def _cli_version() -> str:
    """설치된 Claude Code 중 가장 새 버전 (기본 설치 폴더와 npm 설치를 본다)."""
    found = []
    try:
        found += os.listdir(os.path.join(HOME, ".local", "share", "claude", "versions"))
    except OSError:
        pass
    pkg = os.path.join(os.environ.get("APPDATA", ""), "npm", "node_modules", "@anthropic-ai", "claude-code",
                       "package.json")
    try:
        found.append(str(read_json(pkg).get("version") or ""))
    except (OSError, ValueError, AttributeError):
        pass
    versions = [v for v in found if _VERSION.fullmatch(v)]
    return max(versions, key=lambda v: tuple(map(int, v.split(".")))) if versions else CLI_VERSION_FALLBACK


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

    used_refresh = oauth["refreshToken"]
    resp = requests.post(TOKEN_URL, timeout=15, json={
        "grant_type": "refresh_token",
        "refresh_token": used_refresh,
        "client_id": CLIENT_ID,
    })
    if resp.status_code in (400, 401, 403):
        raise LoginProblem(t("err_expired", title="Claude"), t("hint_claude_login"))
    resp.raise_for_status()
    tok = resp.json()

    # 그 사이 CLI가 먼저 갱신했거나 다른 계정으로 다시 로그인했으면 파일을 덮어쓰지 않는다
    creds = read_json(CRED_PATH)
    oauth = creds.setdefault("claudeAiOauth", {})
    if oauth.get("refreshToken") != used_refresh:
        log.info("claude: login file changed during renewal; keeping the CLI's login")
        if oauth.get("accessToken") and (oauth.get("expiresAt") or 0) / 1000 > time.time() + 60:
            return oauth["accessToken"], oauth
        return tok["access_token"], oauth
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
        if p.hint == t("hint_claude_login"):
            return _needs_login(usage, p.error)
        return usage.fail(p.error, p.hint)

    usage.plan = _plan(oauth)
    resp = requests.get(USAGE_URL, timeout=15, headers={
        "Authorization": f"Bearer {token}",
        "anthropic-beta": "oauth-2025-04-20",
        "Content-Type": "application/json",
        "User-Agent": f"claude-cli/{_cli_version()} (external, cli)",    # 재설정권을 받으려면 Claude Code로 보여야 한다
    })
    if resp.status_code == 401:
        return _needs_login(usage, t("err_expired", title="Claude"))
    if resp.status_code == 429:
        return usage.fail(t("err_rate_limited"), t("hint_rate_limited"))
    resp.raise_for_status()
    parse(usage, resp.json())
    return usage


def _needs_login(usage: Usage, error: str) -> Usage:
    """다시 로그인해야 할 때: Claude Code가 있으면 카드의 '다시 로그인' 버튼이 그 로그인 명령을 실행한다."""
    usage.fail(error, t("hint_claude_login"))
    usage.login_cmd = _login_cmd()
    if usage.login_cmd:
        usage.hint = t("hint_claude_relogin")
    return usage


def _login_cmd() -> list[str] | None:
    """Claude Code의 구독 로그인 명령 (로그인 방법을 고르지 않고 브라우저 승인 화면으로 바로 간다)."""
    for exe in (shutil.which("claude"), os.path.join(HOME, ".local", "bin", "claude.exe"),
                os.path.join(os.environ.get("APPDATA", ""), "npm", "claude.cmd")):
        if exe and os.path.isfile(exe):
            return [exe, "auth", "login", "--claudeai"]
    return None


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

    usage.reset_credits = _reset_credits(d.get("cedar_ember"))


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


def _reset_credits(ce) -> ResetCredits | None:
    """재설정권(cedar_ember): 받은 재설정권들의 남은 횟수 합과 가장 먼저 끝나는 날.

    서버가 이 계정·요청을 대상으로 보지 않으면(eligible이 아님) 줄 자체를 보여 주지 않는다.
    """
    if not isinstance(ce, dict) or ce.get("eligible") is not True:
        return None
    left = [(as_number(g.get("resets_left")), epoch(g.get("ends_at")))
            for g in ce.get("grants") or [] if isinstance(g, dict)]
    now = time.time()
    left = [(n, end) for n, end in left if n and n > 0 and (end is None or end > now)]   # 이미 끝난 것은 빼고
    ends = [end for _, end in left if end]
    return ResetCredits(int(sum(n for n, _ in left)), min(ends) if ends else None)
