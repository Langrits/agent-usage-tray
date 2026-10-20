"""터미널에서 한 번 조회: python -m agent_usage [claude codex cursor gemini]

서비스를 지정하지 않으면 이 PC에 로그인 정보가 있는 서비스만 조회한다.
"""
import sys

from .format import clock, describe_reset
from .i18n import set_language, t
from .providers import ALL, Options, fetch_all
from .store import settings


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cfg = settings()
    set_language(cfg["language"])
    ids = sys.argv[1:] or [m.ID for m in ALL if m.detect()]
    for u in fetch_all(ids, Options(cfg["auto_refresh_login"])).values():
        print(f"■ {u.title}" + (f" ({u.plan})" if u.plan else "") + (f"  [{u.source}]" if u.source else ""))
        if u.error:
            print(f"   ! {u.error}" + (f" → {u.hint}" if u.hint else ""))
        for w in u.windows:
            print(f"   {w.label:<14} {t('pct_left', pct=f'{w.remaining_pct:.0f}'):<10} {describe_reset(w.resets_at)}"
                  + (f"  ({w.note})" if w.note else ""))
        if u.reset_credits is not None:
            rc = u.reset_credits
            print("   " + (t("rc_count", n=rc.count) + (t("rc_expires", clock=clock(rc.expires_at)) if rc.expires_at else "")
                          if rc.count else t("rc_none")))
        for line in u.extra:
            print(f"   · {line}")


if __name__ == "__main__":
    main()
