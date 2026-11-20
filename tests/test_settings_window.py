"""설정 화면 부품의 계산 테스트 (창은 띄우지 않는다)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _testdata  # noqa: E402,F401  실제 데이터 폴더를 쓰지 않도록 store 보다 먼저

from agent_usage.settings_window import EXTRA, REFRESH_MINUTES, THRESHOLDS, place_x, step_value, switch_image
from agent_usage.theme import LIGHT


class PlaceTest(unittest.TestCase):
    # 작업 영역 0~1920, 설정 창 폭 440, 간격 12
    def test_left_of_the_popup_block(self):
        self.assertEqual(place_x((982, 1908), 440, 12, 0, 1920), 982 - 12 - 440)

    def test_right_when_the_popup_is_at_the_left_edge(self):
        self.assertEqual(place_x((20, 960), 440, 12, 0, 1920), 960 + 12)

    def test_centre_when_neither_side_fits_or_no_popup(self):
        self.assertEqual(place_x((300, 1700), 440, 12, 0, 1920), (1920 - 440) // 2)
        self.assertEqual(place_x(None, 440, 12, 0, 1920), (1920 - 440) // 2)

    def test_secondary_monitor_on_the_left(self):
        self.assertEqual(place_x((-900, -12), 440, 12, -1920, 0), -900 - 12 - 440)


class TestDataFolderTest(unittest.TestCase):
    def test_tests_never_use_a_preset_data_folder(self):
        import agent_usage.store as store
        self.assertTrue(_testdata.is_test_dir(store.DATA_DIR))
        self.assertTrue(_testdata.is_test_dir(os.environ["AGENT_USAGE_DATA"]))


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


class ThemeChoiceTest(unittest.TestCase):
    def test_theme_setting_picks_the_palette(self):
        from unittest import mock

        from agent_usage import theme
        self.assertIs(theme.palette("light"), theme.LIGHT)
        self.assertIs(theme.palette("dark"), theme.DARK)
        with mock.patch.object(theme, "is_light", lambda: True):
            self.assertIs(theme.palette("auto"), theme.LIGHT)          # 자동: Windows 앱 모드를 따른다
        with mock.patch.object(theme, "is_light", lambda: False):
            self.assertIs(theme.palette("auto"), theme.DARK)
            self.assertIs(theme.palette(), theme.DARK)


if __name__ == "__main__":
    unittest.main()
