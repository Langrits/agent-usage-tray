"""Cursor.

Cursor 앱이 저장한 로그인(%APPDATA%/Cursor/User/globalStorage/state.vscdb)을 읽기 전용으로 열어
대시보드와 같은 주소(cursor.com/api/usage-summary)를 조회한다. 결제 주기(한 달) 단위로 초기화된다.
"""
from __future__ import annotations

import os
import sqlite3

import requests

from ..i18n import t
from ..models import Usage, Window
from ._util import Options, epoch, jwt_claims

ID = "cursor"
TITLE = "Cursor"
EXPERIMENTAL = False

STATE_DB = os.path.join(os.environ.get("APPDATA", ""), "Cursor", "User", "globalStorage", "state.vscdb")
SUMMARY_URL = "https://cursor.com/api/usage-summary"

PLAN_NAMES = {"free": "Free", "pro": "Pro", "pro_plus": "Pro+", "ultra": "Ultra",
              "business": "Business", "team": "Team", "enterprise": "Enterprise"}


def detect() -> bool:
    return os.path.exists(STATE_DB)


def _read_state(key: str) -> str | None:
    con = sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True)
    try:
        row = con.execute("select value from ItemTable where key=?", (key,)).fetchone()
        return row[0] if row else None
    finally:
        con.close()


def fetch(opts: Options) -> Usage:
    usage = Usage(ID, TITLE, source=t("source_api"))
    try:
        token = _read_state("cursorAuth/accessToken")
    except sqlite3.Error as e:
        return usage.fail(t("err_storage", title="Cursor", err=e), t("hint_cursor_install"))
    if not token:
        return usage.fail(t("err_no_login", title="Cursor"), t("hint_cursor_login"))

    user_id = jwt_claims(token)["sub"].split("|")[-1]
    resp = requests.get(SUMMARY_URL, timeout=15,
                        cookies={"WorkosCursorSessionToken": f"{user_id}%3A%3A{token}"})
    if resp.status_code in (401, 403):
        return usage.fail(t("err_expired", title="Cursor"), t("hint_cursor_open"))
    resp.raise_for_status()
    parse(usage, resp.json())
    return usage


def parse(usage: Usage, d: dict) -> None:
    usage.plan = PLAN_NAMES.get(d.get("membershipType") or "", d.get("membershipType"))
    individual = d.get("individualUsage") or {}
    plan = individual.get("plan") or {}
    resets_at = epoch(d.get("billingCycleEnd"))
    if d.get("isUnlimited"):
        usage.windows.append(Window("month", t("w_month"), 0.0, resets_at, t("unlimited")))
    elif plan.get("enabled", True):
        notes = []
        if plan.get("autoPercentUsed") is not None:
            notes.append(f"Auto {plan['autoPercentUsed']:.0f}%")
        if plan.get("apiPercentUsed") is not None:
            notes.append(f"API {plan['apiPercentUsed']:.0f}%")
        total = plan.get("totalPercentUsed")
        if total is None and plan.get("limit"):
            total = 100 * (plan.get("used") or 0) / plan["limit"]
        usage.windows.append(Window("month", t("w_month"), float(total or 0), resets_at, " · ".join(notes)))

    on_demand = individual.get("onDemand") or {}
    if on_demand.get("enabled"):
        used, limit = (on_demand.get("used") or 0) / 100, on_demand.get("limit")
        usage.extra.append(t("extra_usage_limit", used=f"{used:.2f}", limit=f"{limit / 100:.0f}") if limit
                           else t("extra_usage", used=f"{used:.2f}"))
