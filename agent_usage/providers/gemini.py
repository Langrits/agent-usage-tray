"""Gemini (Gemini CLI · Google 계정 로그인). 실험적.

Gemini CLI 로그인(~/.gemini/oauth_creds.json)으로 Gemini CLI가 쓰는 Code Assist 주소를 조회한다.
  1) loadCodeAssist  → 프로젝트 ID, 요금제(tier)
  2) retrieveUserQuota → 모델별 하루 요청 한도의 남은 비율·초기화 시각
이 주소는 실제로 막혔는데도 100% 남음으로 답하는 경우가 보고돼 있어 '실험적'으로 표시한다.

접속 토큰은 1시간이면 만료된다. 갱신에는 Gemini CLI의 OAuth 클라이언트 값이 필요한데,
설치형 앱용 공개 값이지만 저장소에 넣지 않으려고 이 PC에 설치된 Gemini CLI 파일에서 찾아 쓴다.
"""
from __future__ import annotations

import os
import re
import shutil
import time

import requests

from ..i18n import t
from ..models import Usage, Window
from ._util import HOME, USER_AGENT, LoginProblem, Options, epoch, read_json, write_json_atomic

ID = "gemini"
TITLE = "Gemini"
EXPERIMENTAL = True

CREDS_PATH = os.path.join(HOME, ".gemini", "oauth_creds.json")
BASE_URL = (os.environ.get("CODE_ASSIST_ENDPOINT") or "https://cloudcode-pa.googleapis.com") + "/v1internal"
TOKEN_URL = "https://oauth2.googleapis.com/token"
MAX_WINDOWS = 4

_CLIENT_ID_RE = re.compile(r"\d{6,}-[a-z0-9]+\.apps\.googleusercontent\.com")
_SECRET_RE = re.compile(r"GOCSPX-[A-Za-z0-9_-]{20,}")
_client_cache: tuple[str, str] | None = None


def detect() -> bool:
    return os.path.exists(CREDS_PATH)


def _cli_roots() -> list[str]:
    roots = []
    exe = shutil.which("gemini")
    if exe:
        roots.append(os.path.join(os.path.dirname(exe), "node_modules", "@google"))
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(os.path.join(appdata, "npm", "node_modules", "@google"))
    return [r for r in dict.fromkeys(roots) if os.path.isdir(r)]


def _client_config() -> tuple[str, str] | None:
    """설치된 Gemini CLI의 .js 파일에서 OAuth 클라이언트 ID·secret을 찾는다 (한 번 찾으면 기억)."""
    global _client_cache
    if _client_cache:
        return _client_cache
    scanned = 0
    for root in _cli_roots():
        for dirpath, _, files in os.walk(root):
            for name in files:
                if not name.endswith((".js", ".mjs", ".cjs")):
                    continue
                path = os.path.join(dirpath, name)
                scanned += 1
                if scanned > 20000:
                    return None
                try:
                    if os.path.getsize(path) > 60_000_000:
                        continue
                    with open(path, encoding="utf-8", errors="ignore") as f:
                        text = f.read()
                except OSError:
                    continue
                if "googleusercontent.com" not in text:
                    continue
                cid, secret = _CLIENT_ID_RE.search(text), _SECRET_RE.search(text)
                if cid and secret:
                    _client_cache = (cid.group(0), secret.group(0))
                    return _client_cache
    return None


def _access_token(opts: Options) -> str:
    try:
        creds = read_json(CREDS_PATH)
    except FileNotFoundError:
        raise LoginProblem(t("err_no_login", title="Gemini CLI"), t("hint_gemini_login"))
    if creds.get("access_token") and (creds.get("expiry_date") or 0) / 1000 > time.time() + 60:
        return creds["access_token"]
    if not opts.auto_refresh_login or not creds.get("refresh_token"):
        raise LoginProblem(t("err_expired", title="Gemini"), t("hint_gemini_run"))
    client = _client_config()
    if not client:
        raise LoginProblem(t("err_expired", title="Gemini"), t("hint_gemini_run"))

    resp = requests.post(TOKEN_URL, timeout=15, data={
        "grant_type": "refresh_token",
        "refresh_token": creds["refresh_token"],
        "client_id": client[0],
        "client_secret": client[1],
    })
    if resp.status_code in (400, 401):
        raise LoginProblem(t("err_expired", title="Gemini"), t("hint_gemini_login"))
    resp.raise_for_status()
    tok = resp.json()

    creds = read_json(CREDS_PATH)
    creds["access_token"] = tok["access_token"]
    creds["expiry_date"] = int((time.time() + int(tok.get("expires_in") or 3600)) * 1000)
    for k in ("id_token", "scope", "token_type", "refresh_token"):
        if tok.get(k):
            creds[k] = tok[k]
    write_json_atomic(CREDS_PATH, creds)
    return creds["access_token"]


def _post(method: str, token: str, body: dict) -> requests.Response:
    return requests.post(f"{BASE_URL}:{method}", json=body, timeout=15, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    })


def fetch(opts: Options) -> Usage:
    usage = Usage(ID, TITLE, source=t("source_api"))
    try:
        token = _access_token(opts)
    except LoginProblem as p:
        return usage.fail(p.error, p.hint)

    resp = _post("loadCodeAssist", token, {"metadata": {
        "ideType": "IDE_UNSPECIFIED", "platform": "PLATFORM_UNSPECIFIED", "pluginType": "GEMINI"}})
    if resp.status_code in (401, 403):
        return usage.fail(t("err_expired", title="Gemini"), t("hint_gemini_login"))
    resp.raise_for_status()
    load = resp.json()
    project = load.get("cloudaicompanionProject")
    if isinstance(project, dict):
        project = project.get("id")

    resp = _post("retrieveUserQuota", token, {"project": project} if project else {})
    if resp.status_code == 429:
        return usage.fail(t("err_rate_limited"), t("hint_rate_limited"))
    resp.raise_for_status()
    parse(usage, load, resp.json())
    return usage


def model_name(model_id: str) -> str:
    """gemini-2.5-flash-lite → 2.5 Flash Lite"""
    words = model_id.removeprefix("models/").removeprefix("gemini-").split("-")
    return " ".join(w if w[:1].isdigit() else w.capitalize() for w in words if w)


def parse(usage: Usage, load: dict, quota: dict) -> None:
    tier = load.get("paidTier") or load.get("currentTier") or {}
    usage.plan = tier.get("name") or tier.get("id")
    credits = sum(int(c.get("creditAmount") or 0) for c in tier.get("availableCredits") or [])
    if credits:
        usage.extra.append(t("gemini_credits", n=f"{credits:,}"))

    seen, windows = set(), []
    for b in quota.get("buckets") or []:
        frac, model = b.get("remainingFraction"), b.get("modelId") or ""
        if frac is None or model.endswith("_vertex") or model in seen:
            continue
        seen.add(model)
        windows.append(Window(f"daily_{model}", t("w_daily_model", model=model_name(model)),
                              (1 - float(frac)) * 100, epoch(b.get("resetTime"))))
    windows.sort(key=lambda w: w.used_pct, reverse=True)   # 많이 쓴 모델부터
    usage.windows = windows[:MAX_WINDOWS]
    if len(windows) > MAX_WINDOWS:
        usage.extra.append(t("more_models", n=len(windows) - MAX_WINDOWS))
    usage.extra.append(t("gemini_note"))
