"""서비스 모듈이 같이 쓰는 도구."""
from __future__ import annotations

import base64
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime

HOME = os.path.expanduser("~")
USER_AGENT = "ai-usage-tray"


@dataclass
class Options:
    auto_refresh_login: bool = True    # 만료된 CLI 로그인을 갱신해 다시 저장할지


class LoginProblem(Exception):
    """로그인 정보가 없거나 만료됨. 화면에 그대로 보여준다."""
    def __init__(self, error: str, hint: str | None = None):
        super().__init__(error)
        self.error, self.hint = error, hint


def epoch(v) -> float | None:
    """ISO 문자열·초·밀리초를 epoch 초로."""
    if isinstance(v, bool) or v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return v / 1000 if v > 1e12 else float(v)
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def jwt_claims(token: str) -> dict:
    part = token.split(".")[1]
    part += "=" * (-len(part) % 4)
    return json.loads(base64.urlsafe_b64decode(part))


def read_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json_atomic(path: str, data: dict) -> None:
    """같은 폴더에 임시 파일로 쓴 뒤 바꿔치기 (쓰는 도중 다른 프로그램이 읽어도 깨지지 않게)."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=os.path.basename(path) + ".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)
