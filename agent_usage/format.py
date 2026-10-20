"""시간 표시 형식."""
from __future__ import annotations

import time
from datetime import datetime

from .i18n import t


def countdown(ts: float | None, now: float | None = None) -> str:
    if not ts:
        return ""
    secs = int(ts - (now or time.time()))
    if secs <= 0:
        return t("cd_done")
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    if days:
        return t("cd_days", d=days, h=hours)
    if hours:
        return t("cd_hours", h=hours, m=mins)
    return t("cd_mins", m=max(mins, 1))


def clock(ts: float | None) -> str:
    """오늘이면 시각만, 아니면 날짜(요일)+시각."""
    if not ts:
        return ""
    dt = datetime.fromtimestamp(round(ts / 60) * 60)   # 서버가 59.9초처럼 줄 때가 있어 분 단위로 반올림
    hm = dt.strftime("%H:%M")
    if dt.date() == datetime.now().date():
        return t("today", hm=hm)
    return t("date", m=dt.month, d=dt.day, wd=t("weekdays")[dt.weekday()], hm=hm)


def describe_reset(ts: float | None) -> str:
    if not ts:
        return ""
    cd = countdown(ts)
    return cd if cd == t("cd_done") else t("resets_in", cd=cd, clock=clock(ts))


def ago(ts: float | None) -> str:
    if not ts:
        return ""
    secs = int(time.time() - ts)
    if secs < 60:
        return t("ago_now")
    if secs < 3600:
        return t("ago_m", m=secs // 60)
    if secs < 86400:
        return t("ago_h", h=secs // 3600)
    return clock(ts)
