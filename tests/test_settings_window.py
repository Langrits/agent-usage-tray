"""설정 화면 부품의 계산 테스트 (창은 띄우지 않는다)."""
import os
import tempfile
import unittest

# 설정 화면은 store 를 불러온다: 실제 데이터 폴더를 건드리지 않도록 먼저 임시 폴더로 정한다
os.environ.setdefault("AGENT_USAGE_DATA", tempfile.mkdtemp(prefix="agent_usage_test_"))

from agent_usage.settings_window import EXTRA, REFRESH_MINUTES, THRESHOLDS, step_value, switch_image
from agent_usage.theme import LIGHT


class StepValueTest(unittest.TestCase):
    def test_moves_one_step(self):
        self.assertEqual(step_value(THRESHOLDS, 20, 1), 25)
        self.assertEqual(step_value(THRESHOLDS, 20, -1), 15)

    def test_value_between_steps_goes_to_the_nearest_step_that_way(self):
        self.assertEqual(step_value(THRESHOLDS, 17, 1), 20)        # 예전에 직접 넣은 값
        self.assertEqual(step_value(THRESHOLDS, 17, -1), 15)

    def test_stays_inside_the_range(self):
        self.assertEqual(step_value(THRESHOLDS, 50, 1), 50)
        self.assertEqual(step_value(REFRESH_MINUTES, 2, -1), 2)
        self.assertEqual(step_value(REFRESH_MINUTES, 90, 1), 60)


class SwitchImageTest(unittest.TestCase):
    def test_size_follows_the_screen_scale(self):
        colors = {**LIGHT, **EXTRA["light"]}
        self.assertEqual(switch_image(True, False, colors, 1.0).size, (44, 28))      # 38×22 + 테두리 자리 3px씩
        self.assertEqual(switch_image(False, True, colors, 1.5).size, (66, 42))


if __name__ == "__main__":
    unittest.main()
