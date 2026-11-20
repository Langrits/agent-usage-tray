"""Codex (ChatGPT 요금제).

1순위: Codex가 쓰는 사용량 주소(chatgpt.com/backend-api/wham/usage). 재설정권 개수도 여기서 온다.
2순위: Codex가 남기는 작업 기록(~/.codex/sessions/**/rollout-*.jsonl)의 rate_limits 기록.
로그인 정보(~/.codex/auth.json)는 Codex 앱·CLI가 갱신하므로 여기서는 읽기만 한다.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import time

import requests

from ..i18n import t
from ..models import ResetCredits, Usage, Window, window_key, window_label
from ._util import HOME, Options, as_number, epoch, read_json

log = logging.getLogger("agent_usage")

ID = "codex"
TITLE = "Codex"
EXPERIMENTAL = False

CODEX_DIR = os.environ.get("CODEX_HOME") or os.path.join(HOME, ".codex")
AUTH_PATH = os.path.join(CODEX_DIR, "auth.json")
USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
# 재설정권 하나하나의 소멸 시각. 읽기만 한다 (뒤에 /consume이 붙은 주소는 재설정권을 쓰는 것이라 부르지 않는다)
RESET_CREDITS_URL = "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits"

PLAN_NAMES = {"plus": "Plus", "pro": "Pro", "prolite": "Pro Lite", "team": "Team",
              "business": "Business", "enterprise": "Enterprise", "free": "Free", "go": "Go"}


def detect() -> bool:
    return os.path.exists(AUTH_PATH) or os.path.isdir(os.path.join(CODEX_DIR, "sessions"))


def _plan(name: str | None) -> str | None:
    return PLAN_NAMES.get(name or "", name)


def fetch(opts: Options) -> Usage:
    try:
        return _from_api()
    except Exception as api_err:  # 주소가 막히거나 바뀌면 작업 기록으로 대신한다
        usage = _from_logs()
        if usage.ok:
            usage.extra.append(t("codex_api_fallback", err=str(api_err)[:80]))
        return usage


def _from_api() -> Usage:
    tokens = read_json(AUTH_PATH)["tokens"]
    headers = {
        "Authorization": f"Bearer {tokens['access_token']}",
        "ChatGPT-Account-Id": tokens["account_id"],
        "User-Agent": "codex_cli_rs",
    }
    resp = requests.get(USAGE_URL, timeout=15, headers=headers)
    if resp.status_code == 401:
        raise RuntimeError(t("err_expired", title="Codex"))
    resp.raise_for_status()
    usage = Usage(ID, TITLE, source=t("source_api"))
    parse(usage, resp.json())
    if usage.reset_credits and usage.reset_credits.count:
        try:                    # 소멸 시각을 못 읽어도 개수는 그대로 보여 준다
            r = requests.get(RESET_CREDITS_URL, timeout=15, headers=headers)
            r.raise_for_status()
            usage.reset_credits.expires_at = credit_expiry(r.json(), time.time())
        except (requests.RequestException, ValueError) as e:
            log.info("codex: reset credit details unavailable: %s", type(e).__name__)
    return usage


def parse(usage: Usage, d: dict) -> None:
    usage.plan = _plan(d.get("plan_type"))
    rl = d.get("rate_limit") or {}
    for w in (rl.get("primary_window"), rl.get("secondary_window")):
        used = as_number((w or {}).get("used_percent"))
        if used is not None:                       # 값이 빠졌으면 0%로 가정하지 않는다
            secs = w.get("limit_window_seconds")
            usage.windows.append(Window(window_key(secs), window_label(secs), used, w.get("reset_at")))
    for extra in d.get("additional_rate_limits") or []:
        _add_extra_limit(usage, extra)

    rc = d.get("rate_limit_reset_credits")
    if isinstance(rc, dict):        # 개수만 온다. 소멸 시각은 RESET_CREDITS_URL에서 따로 읽는다
        usage.reset_credits = ResetCredits(int(as_number(rc.get("available_count")) or 0))
    credits = d.get("credits") or {}
    if credits.get("unlimited"):
        usage.extra.append(t("credits_unlimited"))
    elif credits.get("has_credits"):
        usage.extra.append(t("credits_balance", b=credits.get("balance")))
    if rl.get("limit_reached"):
        usage.extra.append(t("limit_reached"))


def _add_extra_limit(usage: Usage, extra: dict) -> None:
    """모델별 추가 한도. 형태가 일정하지 않아 알 수 있는 것만 붙인다."""
    name = extra.get("limit_name") or extra.get("name") or extra.get("model")
    rl = extra.get("rate_limit") or extra
    for w in (rl.get("primary_window"), rl.get("secondary_window")):
        used = as_number((w or {}).get("used_percent"))
        if used is not None:
            secs = w.get("limit_window_seconds")
            usage.windows.append(Window(f"{name}:{window_key(secs)}", f"{window_label(secs)} ({name})",
                                        used, w.get("reset_at")))


def credit_expiry(d: dict, now: float) -> float | None:
    """재설정권 상세 응답에서 쓸 수 있는(available) 것 중 가장 먼저 소멸하는 시각."""
    ends = [epoch(c.get("expires_at")) for c in d.get("credits") or []
            if isinstance(c, dict) and c.get("status") == "available"]
    ends = [e for e in ends if e and e > now]
    return min(ends) if ends else None


def _from_logs() -> Usage:
    usage = Usage(ID, TITLE, source=t("source_logs"))
    files = sorted(glob.glob(os.path.join(CODEX_DIR, "sessions", "*", "*", "*", "rollout-*.jsonl")),
                   key=os.path.getmtime, reverse=True)[:5]
    for path in files:
        found = _last_rate_limits(path)
        if not found:
            continue
        ts, rl = found
        usage.plan = _plan(rl.get("plan_type"))
        usage.data_at = ts
        for w in (rl.get("primary"), rl.get("secondary")):
            used = as_number((w or {}).get("used_percent"))
            if used is not None:
                secs = (w.get("window_minutes") or 0) * 60
                usage.windows.append(Window(window_key(secs), window_label(secs), used, w.get("resets_at")))
        return usage
    return usage.fail(t("err_no_data", title="Codex"), t("hint_codex_login"))


def _last_rate_limits(path: str):
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    for line in reversed(lines):
        if '"rate_limits"' not in line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        rl = (rec.get("payload") or {}).get("rate_limits")
        if rl:
            return epoch(rec.get("timestamp")) or os.path.getmtime(path), rl
    return None
