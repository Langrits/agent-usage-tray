"""기록 파일 읽기 도구: 긴 경로 처리, 덧붙은 줄만 읽는 캐시, 읽기 전용 SQLite 주소."""
from __future__ import annotations

import json
import os
import pathlib
import threading
from datetime import datetime

_LONG = chr(92) * 2 + "?" + chr(92)    # Windows 긴 경로 접두어 (\\?\)
HEAD_BYTES = 256                       # 파일이 새로 쓰였는지 비교할 앞부분
CHUNK = 4 << 20                        # 큰 기록은 4MB씩 읽는다


def lp(path: str) -> str:
    """260자를 넘는 경로도 열 수 있게 한다 (Cursor 기록 폴더 이름이 길다)."""
    path = os.path.abspath(path)
    return path if os.name != "nt" or path.startswith(_LONG) else _LONG + path


def sqlite_ro_uri(path: str) -> str:
    """경로에 #·? 같은 문자가 있어도 깨지지 않는 읽기 전용 SQLite 주소."""
    return pathlib.Path(os.path.abspath(path)).as_uri() + "?mode=ro"


def epoch(v) -> float | None:
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


def mtime(path: str) -> float:
    try:
        return os.path.getmtime(lp(path))
    except OSError:
        return 0.0


class JsonlCache:
    """경로별로 요약 객체를 기억해 두고, 파일 끝에 새로 붙은 줄만 요약에 더한다.

    요약 객체는 feed(record: dict) 메서드를 가진다. 파일이 줄었거나 앞부분이 바뀌었으면(새로 쓰임)
    처음부터 다시 읽는다. prune()은 이번 수집에서 쓰지 않은 파일을 캐시에서 지운다.

    지금 상태만 알면 되는 큰 기록은 get(..., first=앞 바이트, last=끝 바이트)로 처음 읽을 때 앞·끝만 읽는다.
    건너뛴 부분이 있으면 요약 객체의 gap()을 (있으면) 불러 그 사이를 모른다는 것을 알린다.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._items: dict[str, tuple[int, bytes, object]] = {}   # path -> (읽은 바이트, 앞부분, 요약)
        self._used: set[str] = set()

    def get(self, path: str, factory, first: int = 0, last: int = 0):
        with self._lock:
            self._used.add(path)
            offset, head, summary = self._items.get(path, (0, b"", None))
            try:
                size = os.path.getsize(lp(path))
                with open(lp(path), "rb") as f:
                    current_head = f.read(HEAD_BYTES)
                    if summary is None or size < offset or current_head[:len(head)] != head:
                        offset, summary = 0, factory()
                        if last and size > first + last:
                            if first:
                                f.seek(0)
                                _feed_lines(f.read(first), summary)
                            if hasattr(summary, "gap"):
                                summary.gap()
                            f.seek(size - last - 1)
                            f.readline()                     # 줄 중간이면 그 줄은 버린다 (바로 앞이 줄바꿈이면 그대로)
                            offset = f.tell()
            except OSError:
                return None
            if size > offset:
                offset = self._feed(path, offset, summary)
            self._items[path] = (offset, current_head, summary)
            return summary

    def prune(self) -> None:
        with self._lock:
            for path in [p for p in self._items if p not in self._used]:
                del self._items[path]
            self._used = set()

    def __len__(self) -> int:
        return len(self._items)

    @staticmethod
    def _feed(path: str, offset: int, summary) -> int:
        """offset부터 끝까지 완성된 줄만 넘기고, 다 읽은 위치를 돌려준다 (끝나지 않은 마지막 줄은 다음에)."""
        buf = b""
        with open(lp(path), "rb") as f:
            f.seek(offset)
            while True:
                chunk = f.read(CHUNK)
                if not chunk:
                    break
                buf += chunk
                end = buf.rfind(b"\n")
                if end < 0:
                    continue
                _feed_lines(buf[: end + 1], summary)
                offset += end + 1
                buf = buf[end + 1:]
        return offset


def _feed_lines(data: bytes, summary) -> None:
    """마지막 줄바꿈까지의 완성된 줄만 JSON으로 읽어 넘긴다."""
    for raw in data[: data.rfind(b"\n") + 1].splitlines():
        if not raw.strip():
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if isinstance(rec, dict):
            summary.feed(rec)


def read_json(path: str) -> dict:
    try:
        with open(lp(path), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}

