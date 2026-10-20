"""색·글꼴. Windows 앱 테마(밝게/어둡게)를 따른다. 색 값은 dataviz 기본 팔레트."""
from __future__ import annotations

import winreg

FONT = "Malgun Gothic"
ICON_FONT = "Segoe MDL2 Assets"

# 상태색(남은 양 막대·트레이 아이콘). 색만으로 의미를 전하지 않도록 항상 "% 남음" 글자와 함께 쓴다.
STATUS = {"good": "#0ca30c", "warning": "#fab219", "critical": "#d03b3b", "none": "#898781"}

DARK = {
    "bg": "#0d0d0d", "card": "#1a1a19", "text": "#ffffff", "text2": "#c3c2b7", "muted": "#898781",
    "grid": "#2c2c2a", "axis": "#383835", "track": "#2c2c2a", "border": "#383835", "hover": "#2c2c2a",
    "series": ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"],
}
LIGHT = {
    "bg": "#f9f9f7", "card": "#fcfcfb", "text": "#0b0b0b", "text2": "#52514e", "muted": "#898781",
    "grid": "#e1e0d9", "axis": "#c3c2b7", "track": "#e1e0d9", "border": "#e1e0d9", "hover": "#efeee9",
    "series": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"],
}

# 그래프 선 색은 순위가 아니라 대상(서비스+한도)에 고정한다. 8색을 넘는 대상은 마지막 색을 같이 쓴다.
SERIES_SLOT = {
    ("claude", "five_hour"): 0, ("claude", "seven_day"): 1, ("codex", "weekly"): 2, ("cursor", "month"): 3,
    ("codex", "five_hour"): 4, ("claude", "seven_day_fable"): 5,
}


def series_slot(provider: str, window: str) -> int:
    if (provider, window) in SERIES_SLOT:
        return SERIES_SLOT[(provider, window)]
    if provider == "gemini":
        return 6 if "pro" in window else 7
    return 7


def is_light() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 1
    except OSError:
        return False


def palette() -> dict:
    return LIGHT if is_light() else DARK


def series_color(pal: dict, provider: str, window: str) -> str:
    return pal["series"][series_slot(provider, window)]


def level(remaining: float | None, low_threshold: float) -> str:
    if remaining is None:
        return "none"
    if remaining <= low_threshold:
        return "critical"
    if remaining <= 50:
        return "warning"
    return "good"
