"""서비스 공통 사용량 데이터 구조."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .i18n import t


@dataclass
class Window:
    """한도 하나(5시간, 주간, 이번 달 등)."""
    key: str                      # 기록·알림 구분용 고정 키 (예: "five_hour")
    label: str                    # 화면 표시용 (예: "5시간")
    used_pct: float | None        # 사용한 비율 0~100
    resets_at: float | None       # 초기화 시각 (epoch 초)
    note: str = ""                # 보조 설명 (예: "Auto 32% · API 13%")

    @property
    def remaining_pct(self) -> float | None:
        if self.used_pct is None:
            return None
        # 초기화 시각이 지났으면 아직 새로 조회하지 않았어도 다 찬 것으로 본다
        if self.resets_at and self.resets_at <= time.time():
            return 100.0
        return max(0.0, min(100.0, 100.0 - self.used_pct))


@dataclass
class ResetCredits:
    """사용량 재설정권."""
    count: int
    expires_at: float | None = None   # 가장 먼저 소멸하는 시각


@dataclass
class Usage:
    provider: str                 # 서비스 ID ("claude" 등)
    title: str                    # 표시 이름 ("Claude")
    plan: str | None = None
    windows: list[Window] = field(default_factory=list)
    reset_credits: ResetCredits | None = None   # None = 해당 없음/알 수 없음
    extra: list[str] = field(default_factory=list)
    source: str = ""              # 데이터 출처 표시
    data_at: float = field(default_factory=time.time)   # 데이터 기준 시각
    error: str | None = None
    hint: str | None = None       # 오류 시 사용자가 할 일

    @property
    def ok(self) -> bool:
        return self.error is None

    def most_urgent(self) -> Window | None:
        ws = [w for w in self.windows if w.remaining_pct is not None]
        return min(ws, key=lambda w: w.remaining_pct) if ws else None

    def fail(self, error: str, hint: str | None = None) -> "Usage":
        self.error, self.hint = error, hint
        return self


def window_label(seconds: float | None) -> str:
    if not seconds:
        return t("w_limit")
    hours = seconds / 3600
    if abs(hours - 5) < 0.5:
        return t("w_5h")
    if abs(hours - 168) < 2:
        return t("w_week")
    if hours >= 24 * 27:
        return t("w_month")
    if hours >= 24:
        return t("w_days", n=f"{hours / 24:.0f}")
    return t("w_hours", n=f"{hours:.0f}")


def window_key(seconds: float | None) -> str:
    hours = (seconds or 0) / 3600
    if abs(hours - 5) < 0.5:
        return "five_hour"
    if abs(hours - 168) < 2:
        return "weekly"
    if hours >= 24 * 27:
        return "month"
    return f"w{int(seconds or 0)}"
