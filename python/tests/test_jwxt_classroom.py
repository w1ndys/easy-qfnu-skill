import unittest

from qfnu.jwxt_classroom import (
    BLOCK_START_BOUNDS,
    PERIOD_BLOCKS,
    block_bounds_error,
    expand_record,
    expand_room_name,
    normalize_room_name,
    parse_academic_year,
    parse_semester,
    query_blocks,
    validate_query,
    year_semester_list,
)

# 父页学期下拉的固定样本：含别的学年、未来学年与格式不符的项。
SEMESTER_OPTIONS = (
    "2028-2029-1",
    "2027-2028-2",
    "2026-2027-3",
    "2026-2027-2",
    "2026-2027-1",
    "2025-2026-3",
    "2025-2026-2",
    "2025-2026-1",
)

# 查询目标学期，本学年是 2026-2027。
SELECTED_SEMESTER = "2026-2027-1"

# 本学年学期列表：只含目标学年的秋、春、夏，按季节从秋到夏排列。
EXPECTED_YEAR_SEMESTERS = ["2026-2027-1", "2026-2027-2", "2026-2027-3"]

# 大节范围的允许取值，用于断言拒绝提示里给出了取值。
START_OPTIONS_TEXT = "1/3/6/8/10"
END_OPTIONS_TEXT = "1 到 12"


class ClassroomQueryValidationTest(unittest.TestCase):
    """任务 4.1：参数校验只返回失败结果，不碰网络。"""

    def assert_rejected(self, error, parameter):
        """断言失败结果 ok 为 false，并点出无效的参数名。"""
        self.assertIsNotNone(error)
        self.assertFalse(error["ok"])
        self.assertIn(parameter, error["error"])
        self.assertTrue(error["hint"])
        self.assertNotIn("rooms", error)

    def test_valid_query_returns_parsed_parameters(self):
        params, error = validate_query("2026-2027-1", "6", "8", "1", "1", "2")
        self.assertIsNone(error)
        self.assertEqual(params["semester"], "2026-2027-1")
        self.assertEqual(params["week_start"], 6)
        self.assertEqual(params["week_end"], 8)
        self.assertEqual(params["weekday"], 1)
        self.assertEqual(params["period_start"], 1)
        self.assertEqual(params["period_end"], 2)
        self.assertEqual(params["keyword"], "")

    def test_single_week_keeps_start_and_end_equal(self):
        """只给起始周次时结束周次等于起始周次。"""
        params, error = validate_query("2026-2027-1", 6, None, 1, 1, 2)
        self.assertIsNone(error)
        self.assertEqual(params["week_start"], 6)
        self.assertEqual(params["week_end"], 6)

    def test_empty_week_end_is_treated_as_omitted(self):
        params, error = validate_query("2026-2027-1", "30", "", 7, 10, 12)
        self.assertIsNone(error)
        self.assertEqual(params["week_start"], 30)
        self.assertEqual(params["week_end"], 30)

    def test_week_and_period_boundary_values_are_accepted(self):
        params, error = validate_query("2026-2027-1", 1, 30, 7, 1, 12)
        self.assertIsNone(error)
        self.assertEqual(params["week_start"], 1)
        self.assertEqual(params["week_end"], 30)
        self.assertEqual(params["weekday"], 7)

    def test_invalid_semester_format_is_rejected(self):
        for value in ("", "2026-2027", "2026-2027-4", "2026-2027-1-1", "全部"):
            _params, error = validate_query(value, 6, 6, 1, 1, 2)
            self.assert_rejected(error, "semester")

    def test_week_start_out_of_range_is_rejected(self):
        for value in ("", 0, 31, -1, "abc", 6.5):
            _params, error = validate_query("2026-2027-1", value, None, 1, 1, 2)
            self.assert_rejected(error, "week_start")

    def test_week_end_out_of_range_is_rejected(self):
        # 空串表示省略结束周次，不算越界，所以这里只放真正的越界值。
        for value in (0, 31, -1, "abc", 6.5):
            _params, error = validate_query("2026-2027-1", 6, value, 1, 1, 2)
            self.assert_rejected(error, "week_end")

    def test_week_start_after_week_end_is_rejected(self):
        _params, error = validate_query("2026-2027-1", 8, 6, 1, 1, 2)
        self.assert_rejected(error, "week_start")
        self.assertIn("week_end", error["error"])

    def test_weekday_out_of_range_is_rejected(self):
        for value in ("", 0, 8, "星期一", -1, 1.5):
            _params, error = validate_query("2026-2027-1", 6, 6, value, 1, 2)
            self.assert_rejected(error, "weekday")

    def test_weekday_multi_day_writing_is_rejected(self):
        """星期只查一天：多天写法会把不同天的格混在一次响应里，一律拒绝。"""
        for value in ("1,3", "1-3", "1、3", "1 3", "1;3"):
            _params, error = validate_query("2026-2027-1", 6, 6, value, 1, 2)
            self.assert_rejected(error, "weekday")
            self.assertIn("单天", error["error"])

    def test_block_aligned_periods_are_accepted(self):
        """起点落在块首就接受，终点写块内任意小节：3–4 与 3–5 覆盖同一块。"""
        ranges = (
            (1, 2), (3, 5), (6, 7), (8, 9), (10, 12),
            (1, 5), (6, 8), (1, 7), (3, 8), (1, 12),
            (3, 4), (1, 4), (1, 11), (6, 9),
        )
        for start, end in ranges:
            params, error = validate_query("2026-2027-1", 6, 6, 1, start, end)
            self.assertIsNone(error, (start, end))
            self.assertEqual(params["period_start"], start)
            self.assertEqual(params["period_end"], end)

    def test_period_off_block_boundary_is_rejected(self):
        """4–4、2–5 这类起点落在块中间的写法不猜用户想查哪一块，拒绝并列出允许取值。"""
        rejected = ((4, 4), (2, 5), (5, 3), (4, 12), (9, 12), (2, 3), (5, 12))
        for start, end in rejected:
            _params, error = validate_query("2026-2027-1", 6, 6, 1, start, end)
            self.assert_rejected(error, "period_start")
            self.assertIn("period_end", error["error"])
            self.assertIn(START_OPTIONS_TEXT, error["error"])
            self.assertIn(END_OPTIONS_TEXT, error["error"])

    def test_period_out_of_range_is_rejected(self):
        for start, end in (("", 2), ("第1节", 2), (0, 12), (1, 13), (1, 0), (13, 12)):
            _params, error = validate_query("2026-2027-1", 6, 6, 1, start, end)
            self.assert_rejected(error, "period_start")
            self.assertIn(START_OPTIONS_TEXT, error["error"])
            self.assertIn(END_OPTIONS_TEXT, error["error"])

    def test_period_range_reversed_is_rejected(self):
        """起点落在块首但顺序倒置时闭区间为空。"""
        for start, end in ((3, 2), (6, 5), (10, 9)):
            _params, error = validate_query("2026-2027-1", 6, 6, 1, start, end)
            self.assert_rejected(error, "period_start")
            self.assertIn("period_end", error["error"])
    def test_omitted_period_end_equals_start(self):
        """省略结束大节时结束值等于起始值，只查起始大节所在的那一块。"""
        params, error = validate_query("2026-2027-1", 6, 6, 1, 6, None)
        self.assertIsNone(error)
        self.assertEqual(params["period_start"], 6)
        self.assertEqual(params["period_end"], 6)
        params, error = validate_query("2026-2027-1", 6, 6, 1, 1, "")
        self.assertIsNone(error)
        self.assertEqual(params["period_end"], 1)

    def test_keyword_may_be_empty_and_is_normalized(self):
        params, error = validate_query("2026-2027-1", 6, 6, 1, 1, 2, "")
        self.assertIsNone(error)
        self.assertEqual(params["keyword"], "")
        params, error = validate_query("2026-2027-1", 6, 6, 1, 1, 2, "  数学楼　４０１ ")
        self.assertIsNone(error)
        self.assertEqual(params["keyword"], "数学楼 401")

    def test_rejection_does_not_touch_upstream(self):
        """校验失败只返回失败结果，调用方拿不到可发请求的参数。"""
        rejected = ((31, None, 1, 1, 2), (6, 6, 1, 4, 4), (6, 6, 1, 2, 5), (6, 6, 1, 0, 12))
        for week_start, week_end, weekday, start, end in rejected:
            params, error = validate_query("2026-2027-1", week_start, week_end, weekday, start, end)
            self.assertIsNone(params)
            self.assertEqual(error["source"], "jwxt")


class ClassroomYearSemesterTest(unittest.TestCase):
    """任务 4.1：学期解析与本学年学期列表。"""

    def test_parse_semester_keeps_start_year_and_term(self):
        self.assertEqual(parse_semester("2026-2027-1"), (2026, 1))
        self.assertEqual(parse_semester(" 2026-2027-3 "), (2026, 3))
        self.assertEqual(parse_semester("2025-2026-2"), (2025, 2))

    def test_parse_semester_rejects_malformed_values(self):
        for value in ("", "全部", "2026-2027", "2026-2027-4", "2026-2027-ab"):
            self.assertIsNone(parse_semester(value))
        self.assertIsNone(parse_semester(None))

    def test_parse_academic_year_takes_the_first_two_segments(self):
        self.assertEqual(parse_academic_year("2026-2027-1"), "2026-2027")
        self.assertEqual(parse_academic_year(" 2026-2027-3 "), "2026-2027")
        # 末位季节不同不影响学年，换了学年就不是同一个本学年。
        self.assertEqual(parse_academic_year("2026-2027-2"), "2026-2027")
        self.assertNotEqual(parse_academic_year("2027-2028-1"), "2026-2027")

    def test_parse_academic_year_rejects_malformed_values(self):
        for value in ("", "全部", "2026-2027", "2026-2027-4"):
            self.assertIsNone(parse_academic_year(value))
        self.assertIsNone(parse_academic_year(None))

    def test_year_list_keeps_only_the_target_academic_year(self):
        semesters, warnings = year_semester_list(SEMESTER_OPTIONS, SELECTED_SEMESTER)
        self.assertEqual(semesters, EXPECTED_YEAR_SEMESTERS)
        self.assertEqual(warnings, [])
        for other in ("2028-2029-1", "2027-2028-2", "2025-2026-3", "2025-2026-2", "2025-2026-1"):
            self.assertNotIn(other, semesters)

    def test_year_list_is_the_same_from_any_term_of_that_year(self):
        """目标学期是春季或夏季时，取到的还是同一个学年的学期。"""
        for target in ("2026-2027-2", "2026-2027-3"):
            semesters, warnings = year_semester_list(SEMESTER_OPTIONS, target)
            self.assertEqual(semesters, EXPECTED_YEAR_SEMESTERS)
            self.assertEqual(warnings, [])

    def test_year_list_skips_missing_summer(self):
        """下拉里没有夏季项时列表只有秋、春两项。"""
        options = ("2026-2027-2", "2026-2027-1", "2025-2026-1")
        semesters, warnings = year_semester_list(options, SELECTED_SEMESTER)
        self.assertEqual(semesters, ["2026-2027-1", "2026-2027-2"])
        self.assertEqual(warnings, [])

    def test_year_list_skips_malformed_options_with_warning(self):
        options = ("全部", "", "2026-2027-0", SELECTED_SEMESTER, "2026-2027-2")
        semesters, warnings = year_semester_list(options, SELECTED_SEMESTER)
        self.assertEqual(semesters, ["2026-2027-1", "2026-2027-2"])
        self.assertEqual(
            warnings,
            ["学期下拉项格式不符，已跳过: 全部", "学期下拉项格式不符，已跳过: 2026-2027-0"],
        )

    def test_year_list_does_not_depend_on_dropdown_order(self):
        options = ("2026-2027-3", "2026-2027-2", "2026-2027-1")
        semesters, _warnings = year_semester_list(options, SELECTED_SEMESTER)
        self.assertEqual(semesters, EXPECTED_YEAR_SEMESTERS)

    def test_year_list_rejects_page_without_target_semester(self):
        for target in ("", "全部", None):
            semesters, warnings = year_semester_list(SEMESTER_OPTIONS, target)
            self.assertIsNone(semesters)
            self.assertEqual(warnings, [])

    def test_year_list_rejects_dropdown_without_that_year(self):
        """该学年一项都没有时拒绝查询，也不把别的学年的学期顶上。"""
        for options in ((), ("2025-2026-1", "2025-2026-2"), ("2027-2028-1",)):
            semesters, _warnings = year_semester_list(options, SELECTED_SEMESTER)
            self.assertIsNone(semesters)


class ClassroomBlockRangeTest(unittest.TestCase):
    """任务 4.2：大节对齐与覆盖块。"""

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

    def test_block_start_bounds_are_the_first_period_of_each_block(self):
        """起点只能是各块的第一小节；终点不再受块限制，因此没有对应的常量。"""
        self.assertEqual(BLOCK_START_BOUNDS, (1, 3, 6, 8, 10))

    def test_block_bounds_error_names_allowed_values(self):
        self.assertIsNone(block_bounds_error(1, 12))
        self.assertIsNone(block_bounds_error(6, 8))
        self.assertIsNone(block_bounds_error(3, 4))
        for start, end in ((4, 4), (2, 5), (2, 3), (0, 12), (1, 13), (None, 5), (3, None)):
            message = block_bounds_error(start, end)
            self.assertIsNotNone(message, (start, end))
            self.assertIn(START_OPTIONS_TEXT, message)
            self.assertIn(END_OPTIONS_TEXT, message)

    def test_block_bounds_error_reports_reversed_blocks(self):
        self.assertEqual(block_bounds_error(3, 2), "起始大节不能大于结束大节")

    def test_query_blocks_returns_covered_blocks(self):
        self.assertEqual(query_blocks(1, 2), ["0102"])
        self.assertEqual(query_blocks(3, 5), ["030405"])
        self.assertEqual(query_blocks(1, 5), ["0102", "030405"])
        self.assertEqual(query_blocks(6, 8), ["0607", "0809"])
        self.assertEqual(query_blocks(1, 7), ["0102", "030405", "0607"])
        self.assertEqual(query_blocks(3, 8), ["030405", "0607", "0809"])
        self.assertEqual(query_blocks(1, 11), ["0102", "030405", "0607", "0809", "101112"])
        self.assertEqual(query_blocks(1, 12), ["0102", "030405", "0607", "0809", "101112"])

    def test_end_inside_a_block_covers_that_whole_block(self):
        """终点写块内任意小节都按整块纳入：3–3、3–4、3–5 的覆盖块相同。"""
        self.assertEqual(query_blocks(6, 8), ["0607", "0809"])
        self.assertEqual(query_blocks(6, 8), query_blocks(6, 9))
        self.assertEqual(query_blocks(3, 3), ["030405"])
        self.assertEqual(query_blocks(3, 4), query_blocks(3, 5))

    def test_query_blocks_accepts_string_parameters(self):
        self.assertEqual(query_blocks("1", "5"), ["0102", "030405"])

    def test_query_blocks_rejects_ranges_off_block_boundary(self):
        # 起点必须是某块的第一小节；终点越界或起止倒置时同样拼不出整块。
        rejected = ((4, 4), (2, 5), (5, 3), (4, 12), (9, 12), (2, 3), (0, 12), (1, 13), ("", 2), (1, ""), (3, None))
        for start, end in rejected:
            self.assertEqual(query_blocks(start, end), [], (start, end))

    def test_query_blocks_rejects_aligned_but_reversed(self):
        self.assertEqual(query_blocks(3, 2), [])
        self.assertEqual(query_blocks(10, 9), [])

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


if __name__ == "__main__":
    unittest.main()
