"""테스트가 쓰는 데이터 폴더. agent_usage.store 를 불러오기 전에 import 한다.

미리 정해진 AGENT_USAGE_DATA 가 있어도 쓰지 않고, 테스트가 직접 만든 임시 폴더로 덮어쓴다
(실제 데이터 폴더를 가리키고 있으면 기록을 지우는 테스트가 실제 기록을 지울 수 있다).
이 모듈은 한 번만 실행되므로 모든 테스트 파일이 같은 폴더를 쓴다.
"""
import os
import tempfile

DIR = tempfile.mkdtemp(prefix="agent_usage_test_")
os.environ["AGENT_USAGE_DATA"] = DIR


def is_test_dir(path: str) -> bool:
    """기록을 지우는 테스트 전에: 그 폴더가 이 테스트가 만든 임시 폴더인지."""
    return os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(DIR))
