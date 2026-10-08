import unittest

from qfnu.jwxt_classroom import (
    PERIOD_BLOCKS,
    expand_record,
    expand_room_name,
    expected_block_names,
    normalize_room_name,
    parse_semester,
    parse_weeks,
    query_blocks,
    query_cells,
    semester_window,
    validate_query,
)

# 父页学期下拉的固定样本：最前面两项是未来学期。
SEMESTER_OPTIONS = (
    "2028-2029-1",
    "2027-2028-2",
    "2026-2027-3",
    "2026-2027-2",
    "2026-2027-1",
    "2025-2026-3",
    "2025-2026-2",
    "2025-2026-1",
    "2024-2025-3",
    "2024-2025-2",
    "2024-2025-1",
    "2023-2024-3",
    "2023-2024-2",
    "2023-2024-1",
    "2022-2023-3",
    "2022-2023-2",
    "2022-2023-1",
    "2021-2022-2",
    "2021-2022-1",
)

# 页面当前选中学期，窗口上界。
SELECTED_SEMESTER = "2026-2027-1"

# 不晚于选中学期的项有 15 个，窗口只保留最近 13 个，丢掉 2021-2022 的两个。
EXPECTED_WINDOW = (
    "2026-2027-1",
    "2025-2026-3",
    "2025-2026-2",
    "2025-2026-1",
    "2024-2025-3",
    "2024-2025-2",
    "2024-2025-1",
    "2023-2024-3",
    "2023-2024-2",
    "2023-2024-1",
    "2022-2023-3",
    "2022-2023-2",
    "2022-2023-1",
)


class ClassroomQueryValidationTest(unittest.TestCase):
    """任务 1.1：参数校验只返回失败结果，不碰网络。"""

    def assert_rejected(self, error, parameter):
        """断言失败结果 ok 为 false，并点出无效的参数名。"""
        self.assertIsNotNone(error)
        self.assertFalse(error["ok"])
        self.assertIn(parameter, error["error"])
        self.assertTrue(error["hint"])
        self.assertNotIn("rooms", error)

    def test_valid_query_returns_parsed_parameters(self):
        params, error = validate_query("2026-2027-1", "6", "1", "1", "2")
        self.assertIsNone(error)
        self.assertEqual(params["semester"], "2026-2027-1")
        self.assertEqual(params["week"], 6)
        self.assertEqual(params["weekday"], 1)
        self.assertEqual(params["period_start"], 1)
        self.assertEqual(params["period_end"], 2)
        self.assertEqual(params["keyword"], "")

    def test_boundary_values_are_accepted(self):
        params, error = validate_query("2026-2027-1", 1, 7, 12, 12)
        self.assertIsNone(error)
        self.assertEqual(params["week"], 1)
        self.assertEqual(params["weekday"], 7)
        self.assertEqual(params["period_start"], 12)
        _params, error = validate_query("2026-2027-1", 30, 1, 1, 1)
        self.assertIsNone(error)

    def test_invalid_semester_format_is_rejected(self):
        for value in ("", "2026-2027", "2026-2027-4", "2026-2027-1-1", "全部"):
            _params, error = validate_query(value, 6, 1, 1, 2)
            self.assert_rejected(error, "semester")

    def test_week_out_of_range_is_rejected(self):
        for value in ("", 0, 31, -1, "abc", 6.5):
            _params, error = validate_query("2026-2027-1", value, 1, 1, 2)
            self.assert_rejected(error, "week")

    def test_weekday_out_of_range_is_rejected(self):
        for value in ("", 0, 8, "星期一", -1):
            _params, error = validate_query("2026-2027-1", 6, value, 1, 2)
            self.assert_rejected(error, "weekday")

    def test_period_out_of_range_is_rejected(self):
        for value in ("", 0, 13, "第1节", -1):
            _params, error = validate_query("2026-2027-1", 6, 1, value, 2)
            self.assert_rejected(error, "period_start")
            _params, error = validate_query("2026-2027-1", 6, 1, 1, value)
            self.assert_rejected(error, "period_end")

    def test_period_start_after_period_end_is_rejected(self):
        _params, error = validate_query("2026-2027-1", 6, 1, 5, 3)
        self.assert_rejected(error, "period_start")
        self.assertIn("period_end", error["error"])

    def test_keyword_may_be_empty_and_is_normalized(self):
        params, error = validate_query("2026-2027-1", 6, 1, 1, 2, "")
        self.assertIsNone(error)
        self.assertEqual(params["keyword"], "")
        params, error = validate_query("2026-2027-1", 6, 1, 1, 2, "  数学楼　４０１ ")
        self.assertIsNone(error)
        self.assertEqual(params["keyword"], "数学楼 401")

    def test_rejection_does_not_touch_upstream(self):
        """校验失败只返回失败结果，调用方拿不到可发请求的参数。"""
        params, error = validate_query("2026-2027-1", 31, 1, 1, 2)
        self.assertIsNone(params)
        self.assertEqual(error["source"], "jwxt")


class ClassroomSemesterWindowTest(unittest.TestCase):
    """任务 1.2：学期比较与遍历窗口。"""

    def test_parse_semester_keeps_start_year_and_term(self):
        self.assertEqual(parse_semester("2026-2027-1"), (2026, 1))
        self.assertEqual(parse_semester(" 2026-2027-3 "), (2026, 3))
        self.assertEqual(parse_semester("2025-2026-2"), (2025, 2))

    def test_parse_semester_rejects_malformed_values(self):
        for value in ("", "全部", "2026-2027", "2026-2027-4", "2026-2027-ab"):
            self.assertIsNone(parse_semester(value))
        self.assertIsNone(parse_semester(None))

    def test_window_keeps_thirteen_recent_semesters(self):
        window, warnings = semester_window(SEMESTER_OPTIONS, SELECTED_SEMESTER)
        self.assertEqual(window, list(EXPECTED_WINDOW))
        self.assertEqual(len(window), 13)
        self.assertEqual(warnings, [])

    def test_window_uses_all_options_when_fewer_than_thirteen(self):
        options = ("2026-2027-1", "2025-2026-3", "2025-2026-2")
        window, warnings = semester_window(options, SELECTED_SEMESTER)
        self.assertEqual(window, ["2026-2027-1", "2025-2026-3", "2025-2026-2"])
        self.assertEqual(warnings, [])

    def test_window_excludes_semesters_after_selected(self):
        window, _warnings = semester_window(SEMESTER_OPTIONS, SELECTED_SEMESTER)
        self.assertEqual(window[0], "2026-2027-1")
        # 同年末位更大的学期（春季、夏季）和未来学年都晚于选中学期，不进窗口。
        for later in ("2026-2027-2", "2026-2027-3", "2027-2028-2", "2028-2029-1"):
            self.assertNotIn(later, window)

    def test_window_skips_malformed_options_with_warning(self):
        options = ("全部", "", "2026-2027-0", SELECTED_SEMESTER, "2025-2026-3")
        window, warnings = semester_window(options, SELECTED_SEMESTER)
        self.assertEqual(window, ["2026-2027-1", "2025-2026-3"])
        self.assertEqual(
            warnings,
            ["学期下拉项格式不符，已跳过: 全部", "学期下拉项格式不符，已跳过: 2026-2027-0"],
        )

    def test_window_does_not_depend_on_dropdown_order(self):
        options = ("2025-2026-3", "2024-2025-2", SELECTED_SEMESTER)
        window, _warnings = semester_window(options, SELECTED_SEMESTER)
        self.assertEqual(window, ["2026-2027-1", "2025-2026-3", "2024-2025-2"])

    def test_window_rejects_page_without_selected_semester(self):
        for selected in ("", "全部"):
            window, warnings = semester_window(SEMESTER_OPTIONS, selected)
            self.assertIsNone(window)
            self.assertEqual(warnings, [])

    def test_window_of_empty_dropdown_is_empty(self):
        window, warnings = semester_window((), SELECTED_SEMESTER)
        self.assertEqual(window, [])
        self.assertEqual(warnings, [])


class ClassroomBlockAndNameTest(unittest.TestCase):
    """任务 1：固定节次块映射与名称规范化。"""

    def test_period_blocks_follow_design_table(self):
        self.assertEqual(
            PERIOD_BLOCKS,
            (
                ("0102", (1, 2)),
                ("030405", (3, 4, 5)),
                ("0607", (6, 7)),
                ("0809", (8, 9)),
                ("101112", (10, 11, 12)),
            ),
        )

    def test_expected_block_names_repeat_five_blocks_for_seven_days(self):
        names = expected_block_names()
        self.assertEqual(len(names), 35)
        self.assertEqual(names[:5], ["0102", "030405", "0607", "0809", "101112"])
        self.assertEqual(names[5:10], names[:5])
        self.assertEqual(names[30:], names[:5])

    def test_query_blocks_takes_whole_block_on_any_overlap(self):
        self.assertEqual(query_blocks(1, 2), ["0102"])
        self.assertEqual(query_blocks(3, 3), ["030405"])
        self.assertEqual(query_blocks(2, 3), ["0102", "030405"])
        self.assertEqual(query_blocks(11, 12), ["101112"])
        self.assertEqual(query_blocks(1, 12), ["0102", "030405", "0607", "0809", "101112"])

    def test_query_blocks_rejects_empty_or_invalid_range(self):
        self.assertEqual(query_blocks(5, 3), [])
        self.assertEqual(query_blocks(0, 2), [])
        self.assertEqual(query_blocks(1, 13), [])
        self.assertEqual(query_blocks("", 2), [])

    def test_query_cells_repeat_blocks_for_each_weekday(self):
        cells = query_cells(1, 2)
        self.assertEqual(len(cells), 7)
        self.assertEqual([cell["weekday"] for cell in cells], [1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(cells[0]["periods"], [1, 2])
        self.assertTrue(all(cell["block"] == "0102" for cell in cells))
        full = query_cells(1, 12)
        self.assertEqual(len(full), 35)
        self.assertEqual([cell["block"] for cell in full[:5]], expected_block_names()[:5])
        self.assertEqual([cell["weekday"] for cell in full[:5]], [1, 1, 1, 1, 1])

    def test_normalize_room_name_trims_and_collapses_whitespace(self):
        self.assertEqual(normalize_room_name("  格物楼B101 "), "格物楼B101")
        self.assertEqual(normalize_room_name("格物楼  B101"), "格物楼 B101")
        self.assertEqual(normalize_room_name("格物楼\u3000B101"), "格物楼 B101")
        self.assertEqual(normalize_room_name(""), "")
        self.assertEqual(normalize_room_name(None), "")

    def test_normalize_room_name_converts_fullwidth_digits(self):
        self.assertEqual(normalize_room_name("数学楼４０１"), "数学楼401")
        self.assertEqual(normalize_room_name("ＪＣ１００３"), "ＪＣ1003")

    def test_normalize_room_name_keeps_letter_suffix_rooms_apart(self):
        self.assertNotEqual(normalize_room_name("JC1003"), normalize_room_name("JC1003a"))
        self.assertEqual(normalize_room_name("JC1003a"), "JC1003a")


class ClassroomRoomExpansionTest(unittest.TestCase):
    """任务 2.1：合称展开，供字典 jsmc 与课表行首名称共用。"""

    def rooms_of(self, name):
        """取展开出的单体教室，并断言这次展开成功。"""
        expanded = expand_room_name(name)
        self.assertTrue(expanded["expanded"], name)
        return expanded["rooms"]

    def test_dunhao_expands_to_two_rooms(self):
        self.assertEqual(self.rooms_of("数学楼401、403"), ["数学楼401", "数学楼403"])

    def test_dunhao_expansion_does_not_add_middle_rooms(self):
        self.assertNotIn("数学楼402", self.rooms_of("数学楼401、403"))

    def test_fullwidth_separator_is_not_a_separator(self):
        """设计只把顿号、半角句点和空白当分隔符，全角句点不参与切开。"""
        self.assertEqual(self.rooms_of("化学楼127.129"), ["化学楼127", "化学楼129"])

    def test_hyphen_inherits_letter_head_from_previous_room(self):
        self.assertEqual(self.rooms_of("F101-102"), ["F101", "F102"])

    def test_hyphen_keeps_letter_head_written_in_second_room(self):
        self.assertEqual(self.rooms_of("F128-F129"), ["F128", "F129"])

    def test_hyphen_expansion_does_not_add_middle_rooms(self):
        self.assertEqual(self.rooms_of("数学楼401-403"), ["数学楼401", "数学楼403"])

    def test_dunhao_inherits_building_prefix(self):
        self.assertEqual(
            self.rooms_of("实验中心B区B104、B106"),
            ["实验中心B区B104", "实验中心B区B106"],
        )

    def test_single_room_name_stays_single(self):
        self.assertEqual(self.rooms_of("格物楼B101"), ["格物楼B101"])
        self.assertEqual(self.rooms_of("  格物楼B101  "), ["格物楼B101"])
        self.assertEqual(self.rooms_of("数学楼 401"), ["数学楼401"])

    def test_hyphen_is_kept_when_one_side_has_no_room_number(self):
        """`-` 只在两侧都能取出房号时切开，南-101 整体当作一个展示名。"""
        self.assertEqual(self.rooms_of("北-101"), ["北-101"])

    def test_letter_suffix_rooms_stay_two_rooms(self):
        self.assertEqual(self.rooms_of("JC1003"), ["JC1003"])
        self.assertEqual(self.rooms_of("JC1003a"), ["JC1003a"])
        self.assertNotEqual(self.rooms_of("JC1003"), self.rooms_of("JC1003a"))

    def test_unexpandable_name_is_flagged_and_kept_for_warning(self):
        """展开失败时保留原始展示名，rooms 为空，调用方不得把它放进不上课结果。"""
        expanded = expand_room_name("演播厅")
        self.assertFalse(expanded["expanded"])
        self.assertEqual(expanded["rooms"], [])
        self.assertEqual(expanded["name"], "演播厅")

    def test_empty_name_is_unexpandable(self):
        for value in ("", "   ", None):
            expanded = expand_room_name(value)
            self.assertFalse(expanded["expanded"])
            self.assertEqual(expanded["rooms"], [])

    def test_record_keeps_jsid_in_source_jsid(self):
        """合称记录的 jsid 只记在 source_jsid 上，不拆成多个 ID。"""
        record = expand_record("DB3511C3DF574E3A", "数学楼401、403")
        self.assertEqual(record["rooms"], ["数学楼401", "数学楼403"])
        self.assertEqual(record["source_jsid"], "DB3511C3DF574E3A")
        self.assertEqual(record["jsid"], record["source_jsid"])
        self.assertTrue(record["expanded"])

    def test_unexpandable_record_has_no_rooms(self):
        record = expand_record("JSID-1", " 演播厅 ")
        self.assertEqual(record["rooms"], [])
        self.assertFalse(record["expanded"])
        self.assertEqual(record["jsmc"], "演播厅")
        self.assertEqual(record["source_jsid"], "JSID-1")


class ClassroomWeekParsingTest(unittest.TestCase):
    """任务 2.2：课程块周次解析。"""

    def test_range_is_a_closed_interval(self):
        self.assertEqual(parse_weeks("1-3周"), ([1, 2, 3], False))

    def test_single_week_is_one_week(self):
        self.assertEqual(parse_weeks("6周"), ([6], False))
        self.assertEqual(parse_weeks("第6周"), ([6], False))

    def test_comma_and_dunhao_join_intervals(self):
        self.assertEqual(parse_weeks("1-2周,4周"), ([1, 2, 4], False))
        self.assertEqual(parse_weeks("1-2周、4周"), ([1, 2, 4], False))
        self.assertEqual(parse_weeks("1-2周,5-6周"), ([1, 2, 5, 6], False))

    def test_odd_qualifier_keeps_odd_weeks(self):
        self.assertEqual(parse_weeks("1-6周(单)"), ([1, 3, 5], False))
        self.assertEqual(parse_weeks("单周"), (list(range(1, 31, 2)), False))

    def test_even_qualifier_keeps_even_weeks(self):
        self.assertEqual(parse_weeks("1-6周(双)"), ([2, 4, 6], False))
        self.assertEqual(parse_weeks("双周"), (list(range(2, 31, 2)), False))

    def test_qualifier_filters_every_interval_of_the_block(self):
        """限定词作用于该块已解析出的区间，块里没有区间时才作用于 1 到 30。"""
        self.assertEqual(parse_weeks("1-4周(单),6周"), ([1, 3], False))
        self.assertEqual(parse_weeks("单"), (list(range(1, 31, 2)), False))

    def test_odd_and_even_together_do_not_filter(self):
        """一个块里同时写单和双时无法判断各自范围，按不加限定处理。"""
        self.assertEqual(parse_weeks("1-2周(单),3-4周(双)"), ([1, 2, 3, 4], False))

    def test_unparsable_weeks_take_the_whole_semester(self):
        for value in ("", "详见教务", None):
            weeks, unparsed = parse_weeks(value)
            self.assertTrue(unparsed, value)
            self.assertEqual(weeks, list(range(1, 31)))

    def test_out_of_range_and_reversed_expressions_are_dropped(self):
        for value in ("31周", "0周", "16-1周"):
            weeks, unparsed = parse_weeks(value)
            self.assertTrue(unparsed, value)
            self.assertEqual(weeks, list(range(1, 31)))

    def test_week_list_is_sorted_and_unique(self):
        self.assertEqual(parse_weeks("4周,4周,1-2周"), ([1, 2, 4], False))


if __name__ == "__main__":
    unittest.main()
