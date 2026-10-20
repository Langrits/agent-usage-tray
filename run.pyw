"""더블클릭으로 실행 (콘솔 창 없이 트레이에 상주)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_usage.app import main  # noqa: E402

main()
