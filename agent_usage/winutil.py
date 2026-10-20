"""Windows 전용 보조 기능: DPI, 작업 영역, 둥근 모서리, 중복 실행 방지, 자동 실행 등록."""
from __future__ import annotations

import ctypes
import os
import sys
import winreg
from ctypes import wintypes

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "AgentUsageTray"


def enable_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass


def work_area() -> tuple[int, int, int, int]:
    """작업 표시줄을 뺀 주 모니터 영역 (left, top, right, bottom)."""
    rect = wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rect), 0)
    return rect.left, rect.top, rect.right, rect.bottom


def round_corners(tk_window) -> None:
    """Windows 11에서 테두리 없는 창도 둥근 모서리로."""
    try:
        hwnd = ctypes.windll.user32.GetParent(tk_window.winfo_id())
        pref = ctypes.c_int(2)  # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))
    except (AttributeError, OSError):
        pass


def bring_to_front(tk_window) -> None:
    """새 창이 다른 앱 창 뒤에 열리지 않도록 잠깐 맨 위로 올렸다가 되돌린다."""
    tk_window.deiconify()
    tk_window.lift()
    tk_window.attributes("-topmost", True)
    tk_window.after(300, lambda: tk_window.attributes("-topmost", False))
    tk_window.focus_force()


_mutex = None


def already_running() -> bool:
    global _mutex
    _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\AgentUsageTray")
    return ctypes.windll.kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS


def _autostart_command() -> str:
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return f'"{pythonw}" "{os.path.join(PROJECT_DIR, "run.pyw")}"'


def autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
            return True
    except OSError:
        return False


def set_autostart(on: bool) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, _autostart_command())
        else:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except FileNotFoundError:
                pass
