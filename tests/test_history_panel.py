"""사용 기록 패널의 위치 계산과 데이터 묶기 테스트 (창은 띄우지 않는다)."""
import unittest

from agent_usage.history_panel import group_series, panel_x


class PanelPositionTest(unittest.TestCase):
    # 화면 작업 영역: 0 ~ 1920, 팝업 폭 340, 패널 폭 300, 간격 8

    def test_goes_left_of_the_popup_when_there_is_room(self):
        self.assertEqual(panel_x(1560, 340, 300, 8, 0, 1920), 1560 - 8 - 300)

    def test_goes_right_when_the_popup_is_at_the_left_edge(self):
        self.assertEqual(panel_x(20, 340, 300, 8, 0, 1920), 20 + 340 + 8)

    def test_stays_on_screen_when_neither_side_fits(self):
        self.assertEqual(panel_x(100, 340, 300, 8, 0, 500), 0)

    def test_respects_a_secondary_monitor_on_the_left(self):
        self.assertEqual(panel_x(-1500, 340, 300, 8, -1920, 0), -1500 - 8 - 300)


class GroupSeriesTest(unittest.TestCase):
    def test_groups_by_service_and_drops_hidden_or_empty(self):
        series = {("claude", "five_hour"): [(1, 10, "5h")], ("claude", "seven_day"): [(1, 20, "W")],
                  ("codex", "weekly"): [(1, 30, "W")], ("cursor", "month"): [], ("gemini", "daily"): [(1, 5, "D")]}
        out = group_series(series, ["claude", "codex", "cursor"])
        self.assertEqual(sorted(out), ["claude", "codex"])
        self.assertEqual(sorted(out["claude"]), ["five_hour", "seven_day"])


if __name__ == "__main__":
    unittest.main()
