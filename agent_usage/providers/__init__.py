"""서비스 목록. 새 서비스는 이 폴더에 파일을 만들고 아래 ALL에 추가한다 (CONTRIBUTING.md 참고).

각 서비스 모듈에 있어야 하는 것:
  ID: str            설정·기록에 쓰는 고정 키
  TITLE: str         표시 이름
  EXPERIMENTAL: bool 실험적 표시 여부
  detect() -> bool   이 PC에 로그인 정보가 있는지 (네트워크 없이, 빠르게)
  fetch(opts) -> Usage
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from ..i18n import t
from ..models import Usage
from . import claude, codex, cursor, gemini
from ._util import Options

ALL = [claude, codex, cursor, gemini]
BY_ID = {m.ID: m for m in ALL}


def fetch_one(pid: str, opts: Options) -> Usage:
    mod = BY_ID[pid]
    try:
        return mod.fetch(opts)
    except Exception as e:  # 네트워크 오류 등 → 화면에 그대로 보여준다
        return Usage(pid, mod.TITLE).fail(t("err_fetch", err=f"{type(e).__name__}: {e}")[:200], t("hint_fetch"))


def fetch_all(ids: list[str], opts: Options) -> dict[str, Usage]:
    if not ids:
        return {}
    with ThreadPoolExecutor(len(ids)) as pool:
        return dict(zip(ids, pool.map(lambda pid: fetch_one(pid, opts), ids)))


__all__ = ["ALL", "BY_ID", "Options", "fetch_all", "fetch_one"]
