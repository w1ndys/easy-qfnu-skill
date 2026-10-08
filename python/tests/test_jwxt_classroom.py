import json
import os
import random
import tempfile
import unittest
from datetime import datetime, timedelta

from qfnu.jwxt_classroom import (
    BLOCK_START_BOUNDS,
    CACHE_DIR_NAME,
    CACHE_ENV_VAR,
    CACHE_TIMEZONE,
    CACHE_TTL_DAYS,
    GRID_BLOCK_NAMES,
    GRID_CELL_COUNT,
    PERIOD_BLOCKS,
    any_occupied,
    block_bounds_error,
    cache_dir,
    cache_expired,
    dictionary_cache_path,
    expand_record,
    expand_room_name,
    free_blocks_of_day,
    normalize_room_name,
    parse_academic_year,
    parse_classroom_page,
    parse_classroom_table,
    parse_dictionary,
    parse_semester,
    query_blocks,
    read_dictionary_cache,
    read_semester_cache,
    room_occupancy_map,
    row_occupancy,
    semester_cache_path,
    semester_complete,
    validate_query,
    write_dictionary_cache,
    write_semester_cache,
    write_semester_cache_from_page,
    year_semester_list,
)
from qfnu.jwxt_client import state_dir

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



# 父页成功标题，样本里的标题必须与它一致。
PARENT_TITLE = "全校性教室课表"

# 父页样本里的节次模式 ID：故意不用文档里的样本值，用来断言解析不写死样本 ID。
PARENT_MODE_ID = "3F1C9A5E7B20468D"

# 字典请求的 maxRow，截断判据按它比较。
DICTIONARY_MAX_ROW = 5000

# 表头第 1 格原文，与设计里的「教室\节次」一致。
HEADER_FIRST_CELL_TEXT = "教室\\节次"

# 空格格的原文：判空不能靠文字，只能靠格内有没有课程块结构。
EMPTY_CELL = "<nobr> &nbsp; </nobr>"

# 属性测试用的教室名池，都带房号，保证能展开出单体教室。
ROOM_NAME_POOL = ("格物楼B101", "数学楼401", "数学楼401、403", "实验中心B区B104、B106", "F101-102")

# 属性测试用的楼栋前缀池，用来随机拼合称展示名。
BUILDING_POOL = ("数学楼", "格物楼", "实验中心B区")


def course_cell(text):
    """课程格：含 kbcontent 结构即为有课，格内文字不参与判定。"""
    return '<nobr><div id=\'\' class="kbcontent1">' + text + "</div></nobr>"


def grid_cells(occupied, text="课程"):
    """生成 35 个数据格：occupied 里的下标放课程格，其余放原文空格。"""
    cells = []
    for index in range(GRID_CELL_COUNT):
        # 有课与没课只差一个课程块结构，其余一律是空格原文。
        if index in occupied:
            cells.append("<td>" + course_cell(text + str(index)) + "</td>")
        else:
            cells.append("<td>" + EMPTY_CELL + "</td>")
    return cells


def data_row(name, occupied, text="课程"):
    """一行数据行：首格是教室展示名，其后是 35 个数据格。"""
    return name, grid_cells(occupied, text)


def classroom_table(rows, header_first=HEADER_FIRST_CELL_TEXT, block_names=None):
    """拼一份课表响应：星期标题行 + 节次表头行 + 逐行数据。"""
    names = GRID_BLOCK_NAMES if block_names is None else block_names
    day_row = "<tr><th>&nbsp;</th>" + "<th colspan='5'>星期</th>" * 7 + "</tr>"
    header_cells = "".join("<td>" + name + "</td>" for name in names)
    lines = ['<table id="kbtable" border="1">', day_row, "<tr><td>" + header_first + "</td>" + header_cells + "</tr>"]
    for name, cells in rows:
        lines.append("<tr><td>" + name + "</td>" + "".join(cells) + "</tr>")
    lines.append("</table>")
    return "<html><body>" + "\n".join(lines) + "</body></html>"


def parent_page(title=PARENT_TITLE, body="", values=None, mode=PARENT_MODE_ID):
    """拼一份父页样本：标题 + 学期下拉 + 节次模式隐藏字段。"""
    semester_values = SEMESTER_OPTIONS if values is None else values
    options = []
    for value in semester_values:
        attributes = ""
        # 当前学期带 selected，其余项都不带。
        if value == SELECTED_SEMESTER:
            attributes = ' selected="selected"'
        options.append('<option value="' + value + '"' + attributes + ">" + value + "</option>")
    mode_html = ""
    # mode 传 None 表示这份样本故意不带节次模式字段。
    if mode is not None:
        mode_html = '<input type="hidden" name="kbjcmsid" value="' + mode + '">'
    page = (
        "<html><head><title>" + title + "</title></head><body>" + body
        + '<select name="xnxqh">' + "".join(options) + "</select>" + mode_html
        + "</body></html>"
    )
    return page


def occupied_positions(occupancy):
    """取占用位里所有为真的位置，返回 (星期, 块名) 列表。"""
    positions = []
    for weekday in sorted(occupancy):
        for name, _periods in PERIOD_BLOCKS:
            # 只有有课的块才进列表。
            if occupancy[weekday][name]:
                positions.append((weekday, name))
    return positions


def expected_occupancy(occupied):
    """按占用下标算出应有的占用位，供属性测试断言解析没有盖住结构差异。"""
    occupancy = {}
    for weekday in range(1, 8):
        occupancy[weekday] = {}
        for block_index, (name, _periods) in enumerate(PERIOD_BLOCKS):
            # 35 格按天重复 5 个块，下标就是「星期 × 块」的序号。
            occupancy[weekday][name] = ((weekday - 1) * len(PERIOD_BLOCKS) + block_index) in occupied
    return occupancy


class ClassroomParentPageTest(unittest.TestCase):
    """任务 5.1：父页解析覆盖选中学期、kbjcmsid、登录页、缺字段与非法访问。"""

    def assert_rejected(self, error, text):
        """断言失败结果 ok 为 false、错误里点出原因、且不含 rooms。"""
        self.assertIsNotNone(error)
        self.assertFalse(error["ok"])
        self.assertIn(text, error["error"])
        self.assertTrue(error["hint"])
        self.assertNotIn("rooms", error)

    def test_parent_page_reads_semesters_and_selected_one(self):
        """学期下拉的全部 value 与当前选中项都要读出来。"""
        info, error = parse_classroom_page(parent_page())
        self.assertIsNone(error)
        self.assertEqual(info["semesters"], list(SEMESTER_OPTIONS))
        self.assertEqual(info["selected"], SELECTED_SEMESTER)

    def test_parent_page_reads_the_mode_from_the_page(self):
        """节次模式取页面上的值，换一个 ID 也必须原样读出。"""
        info, error = parse_classroom_page(parent_page(mode=PARENT_MODE_ID))
        self.assertIsNone(error)
        self.assertEqual(info["kbjcmsid"], PARENT_MODE_ID)
        other, other_error = parse_classroom_page(parent_page(mode="AABBCCDDEEFF0011"))
        self.assertIsNone(other_error)
        self.assertEqual(other["kbjcmsid"], "AABBCCDDEEFF0011")

    def test_parent_page_without_semester_options_is_rejected(self):
        """学期下拉一个 option 都没有时定不了目标学期，直接拒绝。"""
        info, error = parse_classroom_page(parent_page(values=()))
        self.assertIsNone(info)
        self.assert_rejected(error, "学期")

    def test_first_semester_option_is_selected_without_marker(self):
        """一个 option 都没写 selected 时按浏览器口径取第一项。"""
        page = parent_page(values=("2027-2028-1", "2025-2026-2"))
        info, error = parse_classroom_page(page)
        self.assertIsNone(error)
        self.assertEqual(info["semesters"], ["2027-2028-1", "2025-2026-2"])
        self.assertEqual(info["selected"], "2027-2028-1")

    def test_mode_written_as_select_without_marker_is_read(self):
        """真实父页的节次模式是只有一个 option 的下拉，且不带 selected。"""
        page = (
            "<html><head><title>" + PARENT_TITLE + "</title></head><body>"
            '<select name="xnxqh"><option value="2026-2027-1" selected="selected">2026-2027-1</option></select>'
            '<select name="kbjcmsid"><option value="' + PARENT_MODE_ID + '">默认节次模式</option></select>'
            "</body></html>"
        )
        info, error = parse_classroom_page(page)
        self.assertIsNone(error)
        self.assertEqual(info["kbjcmsid"], PARENT_MODE_ID)

    def test_parent_page_without_mode_is_rejected(self):
        """缺 kbjcmsid 时说明该学期节次模式不可用。"""
        info, error = parse_classroom_page(parent_page(mode=None))
        self.assertIsNone(info)
        self.assert_rejected(error, "kbjcmsid")

    def test_login_page_is_rejected(self):
        """标题写着登录页时停止查询并要求重新登录。"""
        info, error = parse_classroom_page(parent_page(title="统一身份认证登录"))
        self.assertIsNone(info)
        self.assert_rejected(error, "登录")

    def test_login_form_markers_are_rejected_too(self):
        """正文出现登录表单标记时同样按登录页处理。"""
        page = parent_page(body="请输入账号 请输入密码 请输入验证码")
        info, error = parse_classroom_page(page)
        self.assertIsNone(info)
        self.assert_rejected(error, "登录")

    def test_illegal_access_page_is_rejected(self):
        """正文是非法访问时页面未被识别，通常是接口路径误写。"""
        page = "<html><head><title>提示</title></head><body>提示：非法访问！</body></html>"
        info, error = parse_classroom_page(page)
        self.assertIsNone(info)
        self.assert_rejected(error, "非法访问")

    def test_other_page_title_is_rejected(self):
        """标题既不是教室课表也不是登录页时停止查询。"""
        info, error = parse_classroom_page(parent_page(title="学生个人课表"))
        self.assertIsNone(info)
        self.assert_rejected(error, PARENT_TITLE)

    def test_session_kick_page_is_rejected(self):
        """正文是会话互踢提示时与参数无关，只能重新登录。"""
        info, error = parse_classroom_page(parent_page(body="您的账号在其它地方登录"))
        self.assertIsNone(info)
        self.assert_rejected(error, "互踢")


class ClassroomDictionaryTest(unittest.TestCase):
    """任务 5.2：字典只收 jsid 与 jsmc，截断与残缺名单不得当全集。"""

    def records(self, raw_list, max_row=DICTIONARY_MAX_ROW):
        """解析一份字典 JSON 并断言成功，返回字典。"""
        text = json.dumps({"result": True, "list": raw_list})
        dictionary, error = parse_dictionary(text, max_row)
        self.assertIsNone(error)
        return dictionary

    def assert_rejected(self, error, text):
        """断言这次字典被拒绝，错误里点出原因且不含 rooms。"""
        self.assertIsNotNone(error)
        self.assertFalse(error["ok"])
        self.assertIn(text, error["error"])
        self.assertNotIn("rooms", error)

    def test_dictionary_keeps_only_jsid_and_jsmc(self):
        """记录只留 jsid 与 jsmc，其他响应字段不进字典。"""
        items = [{"jsid": "JSID-1", "jsmc": "格物楼B101", "jsbh": "不该出现", "jszt": "1"}]
        dictionary = self.records(items)
        self.assertEqual(dictionary["max_row"], DICTIONARY_MAX_ROW)
        record = dictionary["records"][0]
        self.assertEqual(record["jsid"], "JSID-1")
        self.assertEqual(record["jsmc"], "格物楼B101")
        self.assertEqual(record["rooms"], ["格物楼B101"])
        self.assertEqual(sorted(record), ["expanded", "jsid", "jsmc", "rooms", "source_jsid"])

    def test_dictionary_list_equal_to_max_row_is_rejected(self):
        """list 长度等于 maxRow 说明名单被截断，不能当全集。"""
        items = []
        for index in range(3):
            items.append({"jsid": "JSID-" + str(index), "jsmc": "格物楼B10" + str(index)})
        text = json.dumps({"result": True, "list": items})
        dictionary, error = parse_dictionary(text, 3)
        self.assertIsNone(dictionary)
        self.assert_rejected(error, "maxRow")

    def test_dictionary_result_not_true_is_rejected(self):
        """result 不是 true 时这次请求没有拿到名单。"""
        text = json.dumps({"result": False, "list": []})
        dictionary, error = parse_dictionary(text, DICTIONARY_MAX_ROW)
        self.assertIsNone(dictionary)
        self.assert_rejected(error, "result")

    def test_dictionary_invalid_json_is_rejected(self):
        """响应不是 JSON（例如拿回登录页）时不能当名单。"""
        dictionary, error = parse_dictionary("<html>请输入账号</html>", DICTIONARY_MAX_ROW)
        self.assertIsNone(dictionary)
        self.assert_rejected(error, "JSON")

    def test_dictionary_record_without_jsid_is_skipped(self):
        """缺 jsid 或 jsmc 的记录跳过并记警告。"""
        items = [{"jsmc": "格物楼B101"}, {"jsid": "JSID-2", "jsmc": "数学楼401"}]
        dictionary = self.records(items)
        self.assertEqual(len(dictionary["records"]), 1)
        self.assertEqual(dictionary["records"][0]["jsid"], "JSID-2")
        self.assertTrue(dictionary["warnings"])

    def test_dictionary_unexpandable_name_stays_with_warning(self):
        """展开不出房号的展示名仍留在字典里，但要记警告。"""
        dictionary = self.records([{"jsid": "JSID-3", "jsmc": "演播厅"}])
        self.assertEqual(dictionary["records"][0]["rooms"], [])
        self.assertIn("演播厅", " ".join(dictionary["warnings"]))

    def test_dictionary_composite_record_keeps_one_source_jsid(self):
        """合称记录只记一个 source_jsid，不按展开出的教室拆成多条。"""
        dictionary = self.records([{"jsid": "DB3511C3DF574E3A", "jsmc": "数学楼401、403"}])
        self.assertEqual(len(dictionary["records"]), 1)
        record = dictionary["records"][0]
        self.assertEqual(record["rooms"], ["数学楼401", "数学楼403"])
        self.assertEqual(record["source_jsid"], "DB3511C3DF574E3A")
        self.assertEqual(record["jsid"], record["source_jsid"])


class ClassroomGridTest(unittest.TestCase):
    """任务 5.3：占用位与网格守卫。"""

    def parse_first_row(self, rows, **kwargs):
        """解析一份课表并返回第一行，顺带断言整份响应可用。"""
        parsed, error = parse_classroom_table(classroom_table(rows, **kwargs))
        self.assertIsNone(error)
        return parsed["rows"][0]

    def test_one_room_name_per_data_row(self):
        """一行一个教室名，展开后仍是单体教室。"""
        row = self.parse_first_row([data_row("格物楼B101", set())])
        self.assertEqual(row["name"], "格物楼B101")
        self.assertEqual(row["rooms"], ["格物楼B101"])

    def test_cell_with_kbcontent_counts_as_occupied(self):
        """含 kbcontent 结构的格算有课，落位按「星期 × 块」。"""
        row = self.parse_first_row([data_row("格物楼B101", {0, 11})])
        self.assertEqual(occupied_positions(row["occupancy"]), [(1, "0102"), (3, "030405")])

    def test_nobr_nbsp_cell_is_not_occupied(self):
        """`<nobr> &nbsp; </nobr>` 不算有课，整行 5 块都空闲。"""
        row = self.parse_first_row([data_row("格物楼B101", set())])
        self.assertEqual(occupied_positions(row["occupancy"]), [])
        self.assertEqual(free_blocks_of_day(row["occupancy"], 1), list(GRID_BLOCK_NAMES[:5]))

    def test_occupancy_lands_on_the_weekday_and_block_of_each_cell(self):
        """35 格按 5 个块逐天重复，第 3 天第 2 块因此落在第 12 格（下标 11）。"""
        for index in range(GRID_CELL_COUNT):
            row = self.parse_first_row([data_row("格物楼B101", {index})])
            weekday = index // len(PERIOD_BLOCKS) + 1
            self.assertEqual(occupied_positions(row["occupancy"]), [(weekday, GRID_BLOCK_NAMES[index])])
        row = self.parse_first_row([data_row("格物楼B101", {11})])
        self.assertTrue(row["occupancy"][3]["030405"])
        self.assertFalse(row["occupancy"][3]["0102"])

    def test_selected_block_empty_still_not_occupied(self):
        """同行别的块有内容、但所选大节为空时，该行仍不算占用。"""
        # 第 3 天第 3 块（0607）有课，下标是 2 天 × 5 块 + 2。
        row = self.parse_first_row([data_row("格物楼B101", {12})])
        self.assertTrue(row["occupancy"][3]["0607"])
        self.assertFalse(any_occupied(row["occupancy"], 3, ["0102"]))
        self.assertEqual(free_blocks_of_day(row["occupancy"], 3), ["0102", "030405", "0809", "101112"])

    def test_row_absent_from_response_has_every_block_free(self):
        """占用位为空表示响应里没有这行的教室，5 个块都空闲。"""
        self.assertEqual(free_blocks_of_day({}, 3), list(GRID_BLOCK_NAMES[:5]))

    def test_row_occupancy_guard_requires_35_cells(self):
        """占用位只接受 35 格：格数不符时没有可读的星期与块落位。"""
        self.assertIsNone(row_occupancy(grid_cells({0})[:-1]))
        self.assertEqual(len(row_occupancy(grid_cells(set()))), 7)

    def test_data_row_cell_count_other_than_35_is_unusable(self):
        """格数不是 35 说明课表结构变了，整份响应不可用。"""
        short = grid_cells({0})[:-1]
        long = grid_cells({0}) + ["<td>" + EMPTY_CELL + "</td>"]
        for cells in (short, long):
            parsed, error = parse_classroom_table(classroom_table([("格物楼B101", cells)]))
            self.assertIsNone(parsed)
            self.assertIn("35", error["error"])
            self.assertNotIn("rooms", error)

    def test_header_mismatch_is_unusable(self):
        """表头第 1 格或节次块名不符预期时整份响应不可用。"""
        rows = [data_row("格物楼B101", {0})]
        for kwargs in ({"header_first": "教室/节次"}, {"block_names": GRID_BLOCK_NAMES[:-1]}):
            parsed, error = parse_classroom_table(classroom_table(rows, **kwargs))
            self.assertIsNone(parsed)
            self.assertIn("表头", error["error"])
            self.assertNotIn("rooms", error)

    def test_first_cell_without_room_name_only_warns(self):
        """首格取不出教室名的行只进警告，不进占用集也不产生结果行。"""
        rows = [data_row("演播厅", {0}), data_row("", {0}), data_row("格物楼B101", {0})]
        parsed, error = parse_classroom_table(classroom_table(rows))
        self.assertIsNone(error)
        self.assertEqual(len(parsed["rows"]), 1)
        self.assertEqual(parsed["rows"][0]["name"], "格物楼B101")
        self.assertEqual(room_occupancy_map(parsed).keys(), {"格物楼B101"})
        self.assertEqual(len(parsed["warnings"]), 2)
        self.assertIn("演播厅", " ".join(parsed["warnings"]))

    def test_no_row_with_a_room_name_is_unusable(self):
        """一行都取不出教室名时整份响应不可用，不能当成「都不上课」。"""
        parsed, error = parse_classroom_table(classroom_table([data_row("演播厅", {0})]))
        self.assertIsNone(parsed)
        self.assertIn("教室名", error["error"])
        self.assertNotIn("rooms", error)


class ClassroomContentIndependenceTest(unittest.TestCase):
    """任务 5.4 / 设计 Correctness Properties 第 4 条 / 需求 5.4：判定只依赖首格与课程块结构。"""

    # 固定种子让属性测试可复现。
    SEED = 20261008

    # 轮数，覆盖多种占用组合。
    ROUNDS = 20

    # 两份副本用的格内文字：完全不同，判定不该读出区别。
    WORDS_A = ("语文甲", "数学乙", "体育丙", "自习")
    WORDS_B = ("Engineering", "第二班", "旁听", "占位")

    def random_structures(self, generator):
        """生成随机的教室名与占用下标，两份副本共用这份结构。"""
        structures = []
        for _index in range(generator.randint(1, 4)):
            occupied = set()
            for cell in range(GRID_CELL_COUNT):
                # 每格随机决定有没有课，占用结构就是这条属性要保住的东西。
                if generator.random() < 0.2:
                    occupied.add(cell)
            structures.append((generator.choice(ROOM_NAME_POOL), occupied))
        return structures

    def rendered_table(self, structures, words):
        """按占用结构渲染课表：有课的格放课程块结构，其余格只放文字。"""
        rows = []
        for row_index, (name, occupied) in enumerate(structures):
            cells = []
            for index in range(GRID_CELL_COUNT):
                text = words[(row_index + index) % len(words)]
                # 两份副本只有格内文字不同，课程块结构完全一致。
                if index in occupied:
                    cells.append("<td>" + course_cell(text) + "</td>")
                else:
                    cells.append("<td><nobr> " + text + " </nobr></td>")
            rows.append((name, cells))
        return classroom_table(rows)

    def test_replacing_cell_text_keeps_the_parse_identical(self):
        """格内文字全换掉后，占用集与派生的空闲块完全一致。"""
        generator = random.Random(self.SEED)
        for _round in range(self.ROUNDS):
            structures = self.random_structures(generator)
            first, first_error = parse_classroom_table(self.rendered_table(structures, self.WORDS_A))
            second, second_error = parse_classroom_table(self.rendered_table(structures, self.WORDS_B))
            self.assertIsNone(first_error)
            self.assertIsNone(second_error)
            self.assertEqual(first, second)
            # 文字无关不等于判空失灵：占用位必须与生成的结构逐格吻合。
            for row, (_name, occupied) in zip(first["rows"], structures):
                expected = expected_occupancy(occupied)
                self.assertEqual(row["occupancy"], expected)
                self.assertEqual(free_blocks_of_day(row["occupancy"], 3), free_blocks_of_day(expected, 3))

    def test_clearing_cell_structure_removes_the_occupancy(self):
        """把某行的格清成不含结构后，该行不再算占用，其他行不受影响。"""
        generator = random.Random(self.SEED)
        structures = self.random_structures(generator)
        structures[0] = (structures[0][0], {0, 11})
        parsed, error = parse_classroom_table(self.rendered_table(structures, self.WORDS_A))
        self.assertIsNone(error)
        self.assertEqual(occupied_positions(parsed["rows"][0]["occupancy"]), [(1, "0102"), (3, "030405")])
        cleared = [(structures[0][0], set())] + structures[1:]
        after, after_error = parse_classroom_table(self.rendered_table(cleared, self.WORDS_A))
        self.assertIsNone(after_error)
        self.assertEqual(occupied_positions(after["rows"][0]["occupancy"]), [])
        self.assertEqual(free_blocks_of_day(after["rows"][0]["occupancy"], 1), list(GRID_BLOCK_NAMES[:5]))
        self.assertEqual(after["rows"][1:], parsed["rows"][1:])


class ClassroomCompositeSharingTest(unittest.TestCase):
    """任务 5.5 / 设计 Correctness Properties 第 11 条 / 需求 7.3：合称行对每间单体教室一致。"""

    # 固定种子让属性测试可复现。
    SEED = 20261009

    # 轮数，覆盖多种合称写法与占用组合。
    ROUNDS = 20

    def random_composite(self, generator):
        """随机拼一个合称展示名，并算出它应展开出的单体教室。"""
        building = generator.choice(BUILDING_POOL)
        first = generator.randint(1, 9) * 100 + generator.randint(1, 30)
        second = first + generator.randint(1, 30)
        separator = generator.choice(("、", "."))
        name = building + str(first) + separator + str(second)
        return name, [building + str(first), building + str(second)]

    def random_occupied(self, generator):
        """随机取一组占用下标。"""
        occupied = set()
        for cell in range(GRID_CELL_COUNT):
            # 每格随机决定有没有课。
            if generator.random() < 0.25:
                occupied.add(cell)
        return occupied

    def test_composite_row_shares_one_occupancy_with_every_room(self):
        """合称行展开出的每间单体教室拿到同一份占用位，派生的空闲块也一致。"""
        generator = random.Random(self.SEED)
        for _round in range(self.ROUNDS):
            name, rooms = self.random_composite(generator)
            occupied = self.random_occupied(generator)
            parsed, error = parse_classroom_table(classroom_table([data_row(name, occupied)]))
            self.assertIsNone(error)
            row = parsed["rows"][0]
            self.assertEqual(row["name"], name)
            self.assertEqual(row["rooms"], rooms)
            lookup = room_occupancy_map(parsed)
            for room in rooms:
                # 同一份派生数据：必须是同一个对象，不是逐间各算一遍。
                self.assertIs(lookup[room], row["occupancy"])
            for weekday in range(1, 8):
                first_free = free_blocks_of_day(lookup[rooms[0]], weekday)
                self.assertEqual(first_free, free_blocks_of_day(row["occupancy"], weekday))
                self.assertEqual(free_blocks_of_day(lookup[rooms[1]], weekday), first_free)

    def test_dictionary_composite_record_keeps_one_source_jsid(self):
        """合称字典记录只留一个 source_jsid，不按展开出的教室拆成多条记录。"""
        generator = random.Random(self.SEED)
        items = []
        expected_rooms = []
        for index in range(self.ROUNDS):
            name, rooms = self.random_composite(generator)
            items.append({"jsid": "JSID-" + str(index), "jsmc": name})
            expected_rooms.append(rooms)
        text = json.dumps({"result": True, "list": items})
        dictionary, error = parse_dictionary(text, DICTIONARY_MAX_ROW)
        self.assertIsNone(error)
        self.assertEqual(len(dictionary["records"]), len(items))
        for item, rooms, record in zip(items, expected_rooms, dictionary["records"]):
            self.assertEqual(record["rooms"], rooms)
            self.assertEqual(record["jsid"], item["jsid"])
            self.assertEqual(record["source_jsid"], item["jsid"])


class ClassroomFailurePageTest(unittest.TestCase):
    """任务 5.6 / 需求 8.3、8.4 / 设计 Correctness Properties 第 14 条：失败页不产生教室行。"""

    def assert_unusable(self, raw, text):
        """断言整份响应不可用：失败结果点出原因，且不产生任何教室行。"""
        parsed, error = parse_classroom_table(raw)
        self.assertIsNone(parsed)
        self.assertIsNotNone(error)
        self.assertFalse(error["ok"])
        self.assertIn(text, error["error"])
        self.assertNotIn("rooms", error)

    def test_login_page_is_unusable(self):
        """登录页说明会话已失效，必须重新登录。"""
        page = "<html><head><title>登录</title></head><body>请输入账号 请输入密码</body></html>"
        self.assert_unusable(page, "登录")

    def test_illegal_access_page_is_unusable(self):
        """非法访问页说明接口路径误写，页面未被识别。"""
        self.assert_unusable("<html><body>提示：非法访问！</body></html>", "非法访问")

    def test_session_kick_page_is_unusable(self):
        """会话互踢提示与参数无关，不与课表结构问题混在一起。"""
        self.assert_unusable("<html><body>您的账号在其它地方登录</body></html>", "互踢")

    def test_page_without_kbtable_is_unusable(self):
        """缺 table#kbtable 时拿不到 35 格。"""
        page = "<html><head><title>" + PARENT_TITLE + "</title></head><body><table id='other'></table></body></html>"
        self.assert_unusable(page, "kbtable")

    def test_table_without_closing_tag_is_unusable(self):
        """表格缺闭合标签时不完整，同样不可用。"""
        page = '<table id="kbtable"><tr><td>' + HEADER_FIRST_CELL_TEXT + "</td></tr>"
        self.assert_unusable(page, "kbtable")

    def test_cell_count_other_than_35_is_unusable(self):
        """格数不是 35 说明课表结构变了，不能按缺失的列推断空闲。"""
        rows = [("格物楼B101", grid_cells({0})[:-1])]
        self.assert_unusable(classroom_table(rows), "35")

    def test_period_error_page_is_unusable(self):
        """正文写「查询节次出错」时该学期的节次模式不可用。"""
        page = "<html><body>查询节次出错，请确认是否设置了该学期课表节次！</body></html>"
        self.assert_unusable(page, "节次")

    def test_page_without_any_room_name_is_unusable(self):
        """没有一行首格能取出教室名时不可用，不能当成「都不上课」。"""
        self.assert_unusable(classroom_table([data_row("演播厅", {0})]), "教室名")


# 缓存用例写文件时用的固定时刻，断言文件里的时间戳就按它落。
CACHE_WRITTEN_AT = datetime(2026, 10, 8, 9, 30, tzinfo=CACHE_TIMEZONE)

# 上面那个时刻写出的时间戳文本，格式与设计 Data Models 一致。
CACHE_WRITTEN_TEXT = "2026-10-08T09:30:00+08:00"


class ClassroomCacheTestCase(unittest.TestCase):
    """缓存用例的共同部分：把缓存目录指到临时目录，收尾时恢复环境变量。"""

    def setUp(self):
        """把缓存目录指向临时目录，绝不写真实的 ~/.local/state。"""
        self.original_cache_env = os.environ.get(CACHE_ENV_VAR)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache_root = os.path.join(self.temp.name, "classroom-cache")
        os.environ[CACHE_ENV_VAR] = self.cache_root
        self.addCleanup(self.restore_cache_env)
        # 缓存目录此刻必须已经指向临时目录，后面的写入才落不到真实状态目录。
        self.assertEqual(cache_dir(), self.cache_root)

    def restore_cache_env(self):
        """恢复环境变量，避免影响其他用例。"""
        # 原来没有这个变量时删掉，避免后续用例读到临时目录。
        if self.original_cache_env is None:
            os.environ.pop(CACHE_ENV_VAR, None)
        else:
            os.environ[CACHE_ENV_VAR] = self.original_cache_env

    def read_file(self, path):
        """读缓存文件原文，用来断言文件里没有不该出现的东西。"""
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()

    def parsed_table(self, rows):
        """把 (教室名, 占用下标) 列表拼成课表并解析，返回解析结果。"""
        table = classroom_table([data_row(name, occupied) for name, occupied in rows])
        parsed, error = parse_classroom_table(table)
        self.assertIsNone(error)
        return parsed

    def dictionary_of(self, items):
        """把字典记录拼成 queryJs2 响应并解析，返回字典。"""
        dictionary, error = parse_dictionary(json.dumps({"result": True, "list": items}), DICTIONARY_MAX_ROW)
        self.assertIsNone(error)
        return dictionary


class ClassroomCacheRoundTripTest(ClassroomCacheTestCase):
    """任务 6.1 / 需求 6.6：写入后再读 jsid 与教室名一致，文件里没有 Cookie 与单元格内容。"""

    def test_cache_env_var_moves_the_directory(self):
        """QFNU_CLASSROOM_CACHE_PATH 改的是目录：两个文件名仍由本模块决定。"""
        self.assertEqual(dictionary_cache_path(), os.path.join(self.cache_root, "dictionary.json"))
        self.assertEqual(
            semester_cache_path(SELECTED_SEMESTER),
            os.path.join(self.cache_root, SELECTED_SEMESTER + ".json"),
        )

    def test_default_cache_directory_sits_under_the_state_dir(self):
        """没给环境变量时缓存目录是状态目录下的 classroom-schedule。"""
        os.environ.pop(CACHE_ENV_VAR, None)
        self.assertEqual(cache_dir(), os.path.join(state_dir(), CACHE_DIR_NAME))

    def test_dictionary_round_trip_keeps_jsid_and_room_names(self):
        """写入后再读，每条记录的 jsid、jsmc 与展开出的教室名一致。"""
        items = [
            {"jsid": "DB3511C3DF574E3A8F75F7611C5EAE3B", "jsmc": "格物楼B101"},
            {"jsid": "JSID-2", "jsmc": "数学楼401、403"},
        ]
        written = write_dictionary_cache(self.dictionary_of(items), CACHE_WRITTEN_AT)
        read_back = read_dictionary_cache()
        self.assertEqual(read_back, written)
        self.assertEqual(sorted(read_back), ["fetched_at", "max_row", "record_count", "records", "room_count"])
        self.assertEqual(read_back["fetched_at"], CACHE_WRITTEN_TEXT)
        self.assertEqual(read_back["max_row"], DICTIONARY_MAX_ROW)
        self.assertEqual(read_back["record_count"], 2)
        self.assertEqual(read_back["room_count"], 3)
        self.assertEqual(
            [record["jsid"] for record in read_back["records"]],
            ["DB3511C3DF574E3A8F75F7611C5EAE3B", "JSID-2"],
        )
        self.assertEqual(
            [record["jsmc"] for record in read_back["records"]],
            ["格物楼B101", "数学楼401、403"],
        )
        self.assertEqual(
            [record["rooms"] for record in read_back["records"]],
            [["格物楼B101"], ["数学楼401", "数学楼403"]],
        )
        self.assertEqual(sorted(read_back["records"][0]), ["jsid", "jsmc", "rooms"])

    def test_dictionary_cache_file_keeps_no_cookie_and_no_cell_content(self):
        """字典缓存文件里没有 Cookie、encoded、账号密码，也没有单元格内容。"""
        items = [
            {
                "jsid": "JSID-1",
                "jsmc": "格物楼B101",
                "cookie": "JSESSIONID=SECRET",
                "encoded": "SECRET",
                "password": "SECRET",
            },
            {"jsid": "JSID-2", "jsmc": "数学楼401、403", "kbcontent": "课程甲"},
        ]
        write_dictionary_cache(self.dictionary_of(items), CACHE_WRITTEN_AT)
        body = self.read_file(dictionary_cache_path())
        for forbidden in ("JSESSIONID", "SECRET", "cookie", "Cookie", "encoded", "password", "kbcontent", "课程甲", "<td", "nobr"):
            self.assertNotIn(forbidden, body)
        # 文件确实写在这份缓存目录里，否则上面的断言会因读错文件而假通过。
        self.assertIn("JSID-1", body)

    def test_semester_round_trip_keeps_only_room_names(self):
        """学期缓存只存教室名，行数与教室数按首格展开后的结果落。"""
        parsed = self.parsed_table([("数学楼401、403", {0}), ("格物楼B101", set())])
        written = write_semester_cache(SELECTED_SEMESTER, PARENT_MODE_ID, parsed, CACHE_WRITTEN_AT)
        read_back = read_semester_cache(SELECTED_SEMESTER)
        self.assertEqual(read_back, written)
        self.assertEqual(
            sorted(read_back),
            ["complete", "fetched_at", "kbjcmsid", "room_count", "rooms", "semester", "source_row_count"],
        )
        self.assertEqual(read_back["semester"], SELECTED_SEMESTER)
        self.assertEqual(read_back["kbjcmsid"], PARENT_MODE_ID)
        self.assertEqual(read_back["fetched_at"], CACHE_WRITTEN_TEXT)
        self.assertEqual(read_back["source_row_count"], 2)
        self.assertEqual(read_back["room_count"], 3)
        # 合称行展开成两间单体教室，室名按展示名排序。
        self.assertEqual(read_back["rooms"], ["数学楼401", "数学楼403", "格物楼B101"])
        self.assertFalse(read_back["complete"])
        self.assertTrue(os.path.exists(os.path.join(self.cache_root, SELECTED_SEMESTER + ".json")))

    def test_semester_cache_file_keeps_no_cell_content(self):
        """学期缓存文件只含教室名，不含任何单元格内容。"""
        table = classroom_table([data_row("格物楼B101", {0}, text="课程甲教师乙")])
        payload, error = write_semester_cache_from_page(SELECTED_SEMESTER, PARENT_MODE_ID, table, CACHE_WRITTEN_AT)
        self.assertIsNone(error)
        self.assertIsNotNone(payload)
        body = self.read_file(semester_cache_path(SELECTED_SEMESTER))
        for forbidden in ("课程甲", "教师乙", "kbcontent", "nobr", "<td", "cookie", "Cookie", "encoded"):
            self.assertNotIn(forbidden, body)
        # 文件确实读对了：教室名与学期都在里面。
        self.assertIn("格物楼B101", body)
        self.assertIn(SELECTED_SEMESTER, body)

    def test_cache_missing_or_corrupt_reads_as_none(self):
        """缓存缺失、写坏、字段不全或学期对不上时都按没有缓存处理。"""
        self.assertIsNone(read_dictionary_cache())
        self.assertIsNone(read_semester_cache(SELECTED_SEMESTER))
        # 先写一份正常缓存，再逐种方式把它写坏。
        write_dictionary_cache(self.dictionary_of([{"jsid": "JSID-1", "jsmc": "格物楼B101"}]), CACHE_WRITTEN_AT)
        for broken in ("{", '{"max_row": 5000}', "[]"):
            with open(dictionary_cache_path(), "w", encoding="utf-8") as handle:
                handle.write(broken)
            self.assertIsNone(read_dictionary_cache())
        parsed = self.parsed_table([("格物楼B101", set())])
        write_semester_cache(SELECTED_SEMESTER, PARENT_MODE_ID, parsed, CACHE_WRITTEN_AT)
        # 读另一个学期时文件里的 semester 对不上，不能把这份名单当作它的缓存。
        self.assertIsNone(read_semester_cache("2026-2027-2"))
        with open(semester_cache_path(SELECTED_SEMESTER), "w", encoding="utf-8") as handle:
            handle.write('{"semester": "2026-2027-1", "rooms": "格物楼B101"}')
        self.assertIsNone(read_semester_cache(SELECTED_SEMESTER))


class ClassroomCacheExpiryTest(unittest.TestCase):
    """任务 6 / 设计「缓存」：7 日过期判断是纯函数，写入时间与当前时间都由参数传入。"""

    # 缓存写入时刻，判定只比较它与传入的当前时刻。
    WRITTEN = "2026-10-01T09:30:00+08:00"

    def moment(self, days=0, seconds=0):
        """取写入时刻之后的一段时刻，用来测 7 日边界。"""
        return datetime(2026, 10, 1, 9, 30, tzinfo=CACHE_TIMEZONE) + timedelta(days=days, seconds=seconds)

    def test_ttl_is_seven_days(self):
        self.assertEqual(CACHE_TTL_DAYS, 7)

    def test_fresh_cache_is_not_expired(self):
        self.assertFalse(cache_expired(self.WRITTEN, self.WRITTEN))
        self.assertFalse(cache_expired(self.WRITTEN, self.moment(days=6)))

    def test_exactly_seven_days_is_still_usable(self):
        """正好第 7 天仍算可用：需求口径是早于 7 日才刷新。"""
        self.assertFalse(cache_expired(self.WRITTEN, self.moment(days=7)))
        self.assertFalse(cache_expired(self.WRITTEN, self.moment(days=CACHE_TTL_DAYS)))

    def test_seven_days_and_one_second_is_expired(self):
        self.assertTrue(cache_expired(self.WRITTEN, self.moment(days=7, seconds=1)))

    def test_eight_days_is_expired(self):
        self.assertTrue(cache_expired(self.WRITTEN, self.moment(days=8)))

    def test_datetime_arguments_are_accepted(self):
        """时刻也可以直接用 datetime 传，不必先转成文本。"""
        self.assertFalse(cache_expired(self.moment(), self.moment(days=3)))
        self.assertTrue(cache_expired(self.moment(), self.moment(days=8)))

    def test_timestamps_in_other_offsets_compare_as_moments(self):
        """同一时刻的两种时区写法判定一致，不受写法影响。"""
        # 2026-10-01T01:30:00+00:00 与 2026-10-01T09:30:00+08:00 是同一时刻。
        self.assertFalse(cache_expired("2026-10-01T01:30:00+00:00", "2026-10-08T09:30:00+08:00"))
        self.assertFalse(cache_expired("2026-10-01T01:30:00Z", "2026-10-08T01:30:00Z"))

    def test_unreadable_or_timezone_less_timestamp_is_expired(self):
        """读不出时刻或没带时区时按过期处理：不猜它属于哪个时区。"""
        for value in ("", None, "昨天", 12345, "2026-10-01T09:30:00"):
            self.assertTrue(cache_expired(value, self.moment(days=1)), value)
        self.assertTrue(cache_expired(self.WRITTEN, ""))

    def test_judgement_never_reads_the_system_clock(self):
        """2000 年的两个时刻今天跑仍判为未过期，说明只看传入的两个参数。"""
        self.assertFalse(cache_expired("2000-01-01T00:00:00+08:00", "2000-01-05T00:00:00+08:00"))
        self.assertTrue(cache_expired("2000-01-01T00:00:00+08:00", "2026-10-08T09:30:00+08:00"))


class ClassroomSemesterCompletenessTest(unittest.TestCase):
    """任务 6 / 设计术语「完整学期」：完整性只由教室名数按季节阈值决定。"""

    def test_autumn_and_spring_need_two_hundred_rooms(self):
        for semester in ("2026-2027-1", "2026-2027-2"):
            self.assertFalse(semester_complete(semester, 199))
            self.assertTrue(semester_complete(semester, 200))

    def test_summer_needs_fifty_rooms(self):
        self.assertFalse(semester_complete("2026-2027-3", 49))
        self.assertTrue(semester_complete("2026-2027-3", 50))

    def test_malformed_semester_is_never_complete(self):
        for semester in ("", "全部", None, "2026-2027", "2026-2027-4"):
            self.assertFalse(semester_complete(semester, 5000))


class ClassroomCacheWriteGuardTest(ClassroomCacheTestCase):
    """任务 6 / 需求 2.6、8.3、8.4：残缺结果与失败页都不写缓存。"""

    def test_failed_pages_are_never_cached(self):
        """登录页、会话互踢、非法访问、缺表、非 35 格、节次出错都写不出学期缓存。"""
        pages = (
            ("<html><head><title>登录</title></head><body>请输入账号 请输入密码</body></html>", "登录"),
            ("<html><body>您的账号在其它地方登录</body></html>", "互踢"),
            ("<html><body>提示：非法访问！</body></html>", "非法访问"),
            ("<html><body>查询节次出错，请确认是否设置了该学期课表节次！</body></html>", "节次"),
            (classroom_table([("格物楼B101", grid_cells({0})[:-1])]), "35"),
            (classroom_table([data_row("演播厅", {0})]), "教室名"),
            ("<html><head><title>" + PARENT_TITLE + "</title></head><body><table id='other'></table></body></html>", "kbtable"),
        )
        for page, text in pages:
            payload, error = write_semester_cache_from_page(SELECTED_SEMESTER, PARENT_MODE_ID, page)
            self.assertIsNone(payload)
            self.assertIsNotNone(error)
            self.assertFalse(error["ok"])
            self.assertIn(text, error["error"])
            self.assertNotIn("rooms", error)
            # 正文不可用时缓存文件不该出现。
            self.assertFalse(os.path.exists(semester_cache_path(SELECTED_SEMESTER)))

    def test_writers_refuse_inputs_that_are_not_parse_results(self):
        """解析失败拿到的 None 与其他类型都报类型错误，不落盘。"""
        for value in (None, "html", [], 3):
            with self.assertRaises(TypeError):
                write_semester_cache(SELECTED_SEMESTER, PARENT_MODE_ID, value)
            with self.assertRaises(TypeError):
                write_dictionary_cache(value)
        # 是对象但字段不全（缺 records 的写法）同样不是成功解析的结果。
        with self.assertRaises(TypeError):
            write_dictionary_cache({})
        self.assertFalse(os.path.exists(semester_cache_path(SELECTED_SEMESTER)))
        self.assertFalse(os.path.exists(dictionary_cache_path()))

    def test_writers_refuse_empty_parse_results(self):
        """解析结果里没有数据行或一条记录都没有时同样不写缓存。"""
        for value in ({}, {"rows": []}):
            with self.assertRaises(ValueError):
                write_semester_cache(SELECTED_SEMESTER, PARENT_MODE_ID, value)
        with self.assertRaises(ValueError):
            write_dictionary_cache({"records": []})
        self.assertFalse(os.path.exists(semester_cache_path(SELECTED_SEMESTER)))
        self.assertFalse(os.path.exists(dictionary_cache_path()))

    def test_truncated_dictionary_is_never_cached(self):
        """list 长度等于 maxRow 的截断名单不能当全集，也不允许落盘。"""
        items = [{"jsid": "JSID-1", "jsmc": "格物楼B101"}]
        truncated, error = parse_dictionary(json.dumps({"result": True, "list": items}), 1)
        self.assertIsNone(truncated)
        self.assertIsNotNone(error)
        with self.assertRaises(TypeError):
            write_dictionary_cache(truncated)
        self.assertFalse(os.path.exists(dictionary_cache_path()))

    def test_malformed_semester_has_no_cache_path_to_write(self):
        """学期格式不符时拼不出缓存文件名，写函数报错，也不在缓存目录里留下东西。"""
        parsed = self.parsed_table([("格物楼B101", set())])
        for semester in ("", "全部", "2026-2027", "../escape"):
            self.assertEqual(semester_cache_path(semester), "")
            with self.assertRaises(ValueError):
                write_semester_cache(semester, PARENT_MODE_ID, parsed)
        self.assertFalse(os.path.exists(self.cache_root))


class ClassroomCacheRoundTripPropertyTest(ClassroomCacheTestCase):
    """任务 6.2 / 设计 Correctness Properties 第 13 条 / 需求 2.7、6.6：缓存往返保持字段不变。"""

    # 固定种子让属性测试可复现。
    SEED = 20261012

    # 轮数，覆盖多组随机 jsid、教室名与合称名。
    ROUNDS = 20

    def random_jsid(self, generator):
        """随机生成一个形如教务返回的 32 位十六进制 jsid。"""
        return format(generator.getrandbits(128), "032X")

    def random_room_name(self, generator, index):
        """随机拼一个能展开出房号的展示名：一半是合称，一半是单体名。"""
        building = generator.choice(BUILDING_POOL)
        number = generator.randint(1, 9) * 100 + index
        # 一半的轮次拼成合称，覆盖首格一次给出多间教室的写法。
        if generator.random() < 0.5:
            separator = generator.choice(("、", "."))
            return building + str(number) + separator + str(number + generator.randint(1, 20))
        return building + str(number)

    def test_dictionary_round_trip_keeps_every_jsid_and_room_name(self):
        """字典往返后 jsid、jsmc 与展开出的教室名逐条一致。"""
        generator = random.Random(self.SEED)
        items = []
        expected_ids = []
        expected_names = []
        expected_rooms = []
        for index in range(self.ROUNDS):
            jsid = self.random_jsid(generator)
            name = self.random_room_name(generator, index)
            items.append({"jsid": jsid, "jsmc": name})
            expected_ids.append(jsid)
            expected_names.append(name)
            expected_rooms.append(expand_room_name(name)["rooms"])
        write_dictionary_cache(self.dictionary_of(items), CACHE_WRITTEN_AT)
        read_back = read_dictionary_cache()
        self.assertIsNotNone(read_back)
        self.assertEqual([record["jsid"] for record in read_back["records"]], expected_ids)
        self.assertEqual([record["jsmc"] for record in read_back["records"]], expected_names)
        self.assertEqual([record["rooms"] for record in read_back["records"]], expected_rooms)
        self.assertEqual(read_back["record_count"], self.ROUNDS)
        self.assertEqual(read_back["room_count"], sum(len(rooms) for rooms in expected_rooms))

    def test_semester_round_trip_keeps_every_room_name(self):
        """学期往返后教室名逐字一致，行数与教室数也对得上。"""
        generator = random.Random(self.SEED)
        rows = []
        expected = set()
        for index in range(self.ROUNDS):
            name = self.random_room_name(generator, index)
            rows.append((name, {index % GRID_CELL_COUNT}))
            for room in expand_room_name(name)["rooms"]:
                expected.add(room)
        write_semester_cache(SELECTED_SEMESTER, PARENT_MODE_ID, self.parsed_table(rows), CACHE_WRITTEN_AT)
        read_back = read_semester_cache(SELECTED_SEMESTER)
        self.assertIsNotNone(read_back)
        self.assertEqual(read_back["rooms"], sorted(expected))
        self.assertEqual(read_back["room_count"], len(expected))
        self.assertEqual(read_back["source_row_count"], len(rows))
        self.assertEqual(read_back["fetched_at"], CACHE_WRITTEN_TEXT)


if __name__ == "__main__":
    unittest.main()
