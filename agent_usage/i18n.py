"""화면 문구 (한국어 / English). 언어는 설정값 또는 Windows 표시 언어를 따른다."""
from __future__ import annotations

import ctypes

KO = {
    # 공통
    "app_title": "AI 사용량",
    "pct_left": "{pct}% 남음",
    "resets_in": "{cd} 초기화 · {clock}",
    "reset_done": "초기화됨 · 곧 새로 조회",
    "loading": "불러오는 중…",
    "refreshing": "새로 조회하는 중…",
    "updated": "{ago} 조회 · {n}분마다 자동",
    "no_services": "표시할 서비스가 없어요.\n설정에서 서비스를 켜 주세요.",
    "last_success": " (마지막 성공 {ago})",
    "experimental": "실험적",
    "source_api": "API",
    "source_logs": "작업 기록",
    # 한도 이름
    "w_5h": "5시간", "w_week": "주간", "w_month": "이번 달", "w_limit": "한도",
    "w_days": "{n}일", "w_hours": "{n}시간",
    "w_week_scoped": "주간({name})", "w_week_ext": "주간(외부 앱)", "w_daily_model": "{model} (하루)",
    # 시간
    "cd_days": "{d}일 {h}시간 뒤", "cd_hours": "{h}시간 {m}분 뒤", "cd_mins": "{m}분 뒤", "cd_done": "초기화됨",
    "today": "오늘 {hm}", "date": "{m}/{d}({wd}) {hm}", "weekdays": "월화수목금토일",
    "ago_now": "방금", "ago_m": "{m}분 전", "ago_h": "{h}시간 전",
    # 재설정권·부가 정보
    "rc_count": "재설정권 {n}개", "rc_expires": " · {clock} 소멸", "rc_none": "재설정권 없음",
    "extra_usage": "추가 사용: ${used}", "extra_usage_limit": "추가 사용: ${used} / ${limit}",
    "week_breakdown": "이번 주 사용처: {rows}",
    "credits_unlimited": "크레딧: 무제한", "credits_balance": "크레딧 잔액: {b}", "limit_reached": "현재 한도 도달",
    "codex_api_fallback": "API 조회 실패로 작업 기록 사용 ({err})",
    "unlimited": "무제한", "more_models": "그 밖에 모델 {n}개",
    "gemini_note": "실험적 · 실제 한도와 다를 수 있어요",
    "gemini_credits": "AI 크레딧: {n}",
    # 오류·안내
    "err_fetch": "조회 실패: {err}",
    "hint_fetch": "인터넷 연결을 확인해 주세요. 계속되면 서비스 주소가 바뀐 것일 수 있어요",
    "err_no_login": "{title} 로그인 정보가 없어요",
    "err_expired": "{title} 로그인이 만료됐어요",
    "err_rate_limited": "조회가 너무 잦아 잠시 막혔어요",
    "hint_rate_limited": "몇 분 뒤 자동으로 다시 조회해요",
    "err_storage": "{title} 저장소를 열지 못했어요 ({err})",
    "hint_claude_login": "터미널에서 claude 를 실행하고 /login 으로 로그인해 주세요",
    "hint_claude_run": "터미널에서 claude 를 한 번 실행하면 갱신돼요 (설정에서 자동 갱신을 켤 수도 있어요)",
    "hint_codex_login": "Codex 앱이나 CLI에서 로그인하고 한 번 사용하면 표시돼요",
    "hint_cursor_install": "Cursor 앱이 설치돼 있는지 확인해 주세요",
    "hint_cursor_login": "Cursor 앱에서 로그인해 주세요",
    "hint_cursor_open": "Cursor 앱을 한 번 열면 갱신돼요",
    "hint_gemini_login": "터미널에서 gemini 를 실행해 Google 계정으로 로그인해 주세요",
    "hint_gemini_run": "터미널에서 gemini 를 한 번 실행하면 갱신돼요",
    "err_no_data": "{title} 사용 기록을 찾지 못했어요",
    "err_format": "응답에 사용량 숫자가 없어요 (서비스 형식이 바뀌었을 수 있어요)",
    # 트레이
    "menu_toggle": "열기 / 닫기", "menu_refresh": "지금 새로고침", "menu_pin": "화면에 고정",
    "menu_history": "사용 기록 그래프", "menu_settings": "설정…", "menu_quit": "종료",
    "tooltip": "남은 양 · {parts}", "tooltip_attention": "{title} 확인 필요",
    # 알림
    "n_reset_title": "한도 초기화", "n_reset": "{name} 한도가 초기화됐어요 · 지금 {r}% 남음",
    "n_low_title": "사용량 부족", "n_low": "{name} {r}% 남음", "n_low_when": " · 초기화 {when}",
    "n_rc_title": "재설정권", "n_rc": "{title} 재설정권이 생겼어요 · {n}개 보유",
    "n_many": "AI 사용량 알림 {n}건",
    # 그래프
    "g_title": "AI 사용량 기록", "g_heading": "사용한 비율 (%)", "g_sub": "초기화되면 아래로 떨어져요",
    "g_24h": "24시간", "g_7d": "7일", "g_30d": "30일", "g_table": "표로 보기",
    "g_empty": "아직 기록이 없어요. 몇 번 조회되면 그래프가 그려져요.",
    "g_col_time": "시각", "g_col_item": "항목", "g_col_used": "사용한 비율", "g_hour": "{h}시",
    # 설정
    "s_title": "설정", "s_services": "서비스", "s_alerts": "알림", "s_general": "일반",
    "s_mode_auto": "자동", "s_mode_show": "항상 표시", "s_mode_hide": "숨기기",
    "s_mode_help": "자동: 이 PC에 로그인 정보가 있을 때만 표시",
    "s_connected": "로그인 정보 있음", "s_not_connected": "로그인 정보 없음",
    "s_low": "남은 양이", "s_low_suffix": "% 이하로 내려가면 알림",
    "s_reset": "한도 초기화·재설정권이 생기면 알림",
    "s_refresh": "조회 주기", "s_refresh_suffix": "분마다",
    "s_auto_refresh": "로그인 자동 갱신 (Claude · Gemini)",
    "s_auto_refresh_help": "만료되면 토큰을 갱신해서 각 CLI의 로그인 파일에 다시 저장해요",
    "s_autostart": "윈도우 시작 시 자동 실행",
    "s_language": "언어", "s_lang_auto": "자동 (Windows 언어)",
    "s_data": "데이터 폴더 열기", "s_close": "닫기", "s_version": "버전 {v}",
}

EN = {
    "app_title": "AI Usage",
    "pct_left": "{pct}% left",
    "resets_in": "resets {cd} · {clock}",
    "reset_done": "reset · refreshing soon",
    "loading": "Loading…",
    "refreshing": "Refreshing…",
    "updated": "Updated {ago} · every {n} min",
    "no_services": "Nothing to show.\nTurn on a service in Settings.",
    "last_success": " (last success {ago})",
    "experimental": "beta",
    "source_api": "API",
    "source_logs": "session logs",
    "w_5h": "5-hour", "w_week": "Weekly", "w_month": "This month", "w_limit": "Limit",
    "w_days": "{n}-day", "w_hours": "{n}-hour",
    "w_week_scoped": "Weekly ({name})", "w_week_ext": "Weekly (external apps)", "w_daily_model": "{model} (daily)",
    "cd_days": "in {d}d {h}h", "cd_hours": "in {h}h {m}m", "cd_mins": "in {m}m", "cd_done": "reset",
    "today": "today {hm}", "date": "{wd} {m}/{d} {hm}", "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    "ago_now": "just now", "ago_m": "{m}m ago", "ago_h": "{h}h ago",
    "rc_count": "Reset credits: {n}", "rc_expires": " · expires {clock}", "rc_none": "No reset credits",
    "extra_usage": "Extra usage: ${used}", "extra_usage_limit": "Extra usage: ${used} / ${limit}",
    "week_breakdown": "This week: {rows}",
    "credits_unlimited": "Credits: unlimited", "credits_balance": "Credit balance: {b}", "limit_reached": "Limit reached",
    "codex_api_fallback": "API failed, using session logs ({err})",
    "unlimited": "Unlimited", "more_models": "{n} more models",
    "gemini_note": "Beta · may differ from your actual limits",
    "gemini_credits": "AI credits: {n}",
    "err_fetch": "Fetch failed: {err}",
    "hint_fetch": "Check your connection. If this keeps happening, the service may have changed.",
    "err_no_login": "No {title} login found",
    "err_expired": "{title} login expired",
    "err_rate_limited": "Too many requests, temporarily blocked",
    "hint_rate_limited": "Will retry automatically in a few minutes",
    "err_storage": "Couldn't open {title} storage ({err})",
    "hint_claude_login": "Run `claude` in a terminal and sign in with /login",
    "hint_claude_run": "Run `claude` once in a terminal to renew it (or turn on auto-renew in Settings)",
    "hint_codex_login": "Sign in to the Codex app or CLI and use it once",
    "hint_cursor_install": "Make sure the Cursor app is installed",
    "hint_cursor_login": "Sign in to the Cursor app",
    "hint_cursor_open": "Open the Cursor app once to renew it",
    "hint_gemini_login": "Run `gemini` in a terminal and sign in with Google",
    "hint_gemini_run": "Run `gemini` once in a terminal to renew it",
    "err_no_data": "No {title} usage data found",
    "err_format": "No usage numbers in the response (the service may have changed)",
    "menu_toggle": "Open / Close", "menu_refresh": "Refresh now", "menu_pin": "Pin to screen",
    "menu_history": "Usage history", "menu_settings": "Settings…", "menu_quit": "Quit",
    "tooltip": "Left · {parts}", "tooltip_attention": "{title} needs attention",
    "n_reset_title": "Limit reset", "n_reset": "{name} has reset · {r}% left",
    "n_low_title": "Running low", "n_low": "{name}: {r}% left", "n_low_when": " · resets {when}",
    "n_rc_title": "Reset credit", "n_rc": "{title}: new reset credit · {n} available",
    "n_many": "AI Usage: {n} alerts",
    "g_title": "AI Usage History", "g_heading": "Used (%)", "g_sub": "drops when a limit resets",
    "g_24h": "24h", "g_7d": "7d", "g_30d": "30d", "g_table": "Table",
    "g_empty": "No history yet. The chart appears after a few refreshes.",
    "g_col_time": "Time", "g_col_item": "Item", "g_col_used": "Used", "g_hour": "{h}:00",
    "s_title": "Settings", "s_services": "Services", "s_alerts": "Alerts", "s_general": "General",
    "s_mode_auto": "Auto", "s_mode_show": "Always show", "s_mode_hide": "Hide",
    "s_mode_help": "Auto: show only when this PC has a login for it",
    "s_connected": "Login found", "s_not_connected": "Not signed in",
    "s_low": "Alert when remaining drops to", "s_low_suffix": "% or less",
    "s_reset": "Alert when a limit resets or a reset credit arrives",
    "s_refresh": "Refresh every", "s_refresh_suffix": "min",
    "s_auto_refresh": "Renew logins automatically (Claude · Gemini)",
    "s_auto_refresh_help": "When a token expires, renew it and save it back to that CLI's login file",
    "s_autostart": "Start with Windows",
    "s_language": "Language", "s_lang_auto": "Auto (Windows language)",
    "s_data": "Open data folder", "s_close": "Close", "s_version": "Version {v}",
}

STRINGS = {"ko": KO, "en": EN}
_lang = "en"


def system_language() -> str:
    try:
        langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        return "ko" if langid & 0x3FF == 0x12 else "en"
    except (AttributeError, OSError):
        return "en"


def set_language(choice: str | None) -> None:
    global _lang
    _lang = choice if choice in STRINGS else system_language()


def lang() -> str:
    return _lang


def t(key: str, **kw):
    s = STRINGS[_lang].get(key, EN.get(key, key))
    return s.format(**kw) if kw and isinstance(s, str) else s


set_language(None)
