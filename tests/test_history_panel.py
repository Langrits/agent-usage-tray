"""사용 기록 패널의 위치 계산과 데이터 묶기 테스트 (창은 띄우지 않는다)."""
import unittest

from agent_usage.history_panel import fit_legend, group_series, panel_x, uniform_height


class UniformHeightTest(unittest.TestCase):
    def test_uses_the_shortest_single_row_card(self):
        # Claude(3줄, 262px), Codex(1줄+재설정권, 143px), Cursor(1줄, 140px)
        self.assertEqual(uniform_height([(262, 3), (143, 1), (140, 1)], 150), 140)

    def test_falls_back_when_no_card_has_a_single_row(self):
        self.assertEqual(uniform_height([(262, 3), (80, 0)], 150), 150)


class FitLegendTest(unittest.TestCase):
    measure = staticmethod(lambda s: len(s) * 7)

    def test_names_fit_on_one_line_as_they_are(self):
        labels = ["5시간", "주간", "주간(Fable)"]
        self.assertEqual(fit_legend(labels, 200, self.measure, 22), (labels, 0))

    def test_drops_a_shared_suffix_before_hiding_items(self):
        labels = ["2.5 Pro (하루)", "2.5 Flash (하루)"]
        shown, hidden = fit_legend(labels, 160, self.measure, 22)
        self.assertEqual((shown, hidden), (["2.5 Pro", "2.5 Flash"], 0))

    def test_hides_items_from_the_end_with_a_count(self):
        labels = ["Alpha model", "Beta model", "Gamma model", "Delta model"]
        shown, hidden = fit_legend(labels, 220, self.measure, 22)
        self.assertEqual(shown, ["Alpha model", "Beta model"])
        self.assertEqual(hidden, 2)


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
