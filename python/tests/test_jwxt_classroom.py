import http.client
import json
import os
import random
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

from qfnu.jwxt_classroom import (
    BLOCK_NOTE_TEXT,
    BLOCK_START_BOUNDS,
    CACHE_DIR_NAME,
    CACHE_ENV_VAR,
    CACHE_TIMEZONE,
    CACHE_TTL_DAYS,
    CLASSROOM_DICTIONARY_URL,
    CLASSROOM_IFR_URL,
    CLASSROOM_PAGE_URL,
    CLASSROOM_USER_AGENT,
    DICTIONARY_MAX_ROW,
    FETCH_ATTEMPTS,
    FREE_SWITCHES,
    GRID_BLOCK_NAMES,
    GRID_CELL_COUNT,
    LIMITATION_TEXT,
    MERGED_ROOM_WARNING,
    PERIOD_BLOCKS,
    ROOM_STATUS,
    ROOM_STATUS_TEXT,
    any_occupied,
    block_bounds_error,
    cache_dir,
    cache_expired,
    candidate_rooms,
    dictionary_cache_path,
    dictionary_form,
    dictionary_room_index,
    empty_classroom_result,
    expand_record,
    expand_room_name,
    fetch_dictionary,
    fetch_parent_page,
    fetch_query_page,
    fetch_semester_page,
    filter_free_rooms,
    free_blocks_of_day,
    keyword_skjs,
    normalize_room_name,
    now_moment,
    occupied_rooms,
    parse_academic_year,
    parse_classroom_page,
    parse_classroom_table,
    parse_dictionary,
    parse_semester,
    query_blocks,
    query_empty_classrooms,
    query_form,
    read_dictionary_cache,
    read_semester_cache,
    reset_serial_refresh,
    room_free_info,
    room_occupancy_map,
    row_occupancy,
    selected_rooms,
    semester_cache_path,
    semester_complete,
    semester_evidence_rooms,
    semester_form,
    serial_lock,
    validate_query,
    write_dictionary_cache,
    write_semester_cache,
    write_semester_cache_from_page,
    year_round_idle_rooms,
    year_semester_list,
)
from qfnu.jwxt_client import JWXT_BASE, MAIN_URL, state_dir

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

# 字典请求的 maxRow 直接用模块常量 DICTIONARY_MAX_ROW，截断判据按它比较。

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



# 请求层用例：假客户端记录请求并按队列返回响应，不发任何真实网络请求。


# 学期教室名 POST 里必须为空的 11 个字段。
EMPTY_SEMESTER_FIELDS = (
    "skyx",
    "xqid",
    "jzwid",
    "skjsid",
    "skjs",
    "zc1",
    "zc2",
    "skxq1",
    "skxq2",
    "jc1",
    "jc2",
)

# 学生端路径段必须出现的名字，以及绝不能出现的路径段。
STUDENT_PATH_SEGMENT = "kbcx"
FOREIGN_PATH_SEGMENTS = ("kbxx", "jsxsd.kbxx")

# 会话互踢提示原文：这类完整页面重试也拿不到别的，用来断言不重试。
SESSION_KICKED_TEXT = "您的账号在其它地方登录"

# 精确教室名与楼名关键词的样本：合称记录用来断言写进 skjs 的是未展开原名。
KEYWORD_RECORDS = (
    {"jsid": "JSID-A", "jsmc": "数学楼401"},
    {"jsid": "JSID-B", "jsmc": "数学楼401、403"},
    {"jsid": "JSID-C", "jsmc": "格物楼B101"},
)


class FakeClassroomClient:
    """假教务客户端：记录每次请求，并按队列给出响应或抛出传输异常。"""

    def __init__(self, responses=()):
        # 逐次调用要返回的 (状态码, 正文)，也可以是表示传输失败的异常。
        self.responses = list(responses)
        # 每次请求的 method、url、body 与 headers，供用例断言。
        self.calls = []

    def text(self, method, target, body=None, headers=None, same_origin=False):
        """按 JWXTClient.text 的返回形状给出 (状态码, 最终地址, 正文)。

        状态码转成整数，与真实客户端一致，免得样本里的 "200" 被当成非 200。
        """
        self.calls.append(
            {"method": method, "url": target, "body": body, "headers": headers or {}}
        )
        # 队列里没有响应说明代码多发了一次请求，直接报错而不是继续编造正文。
        if not self.responses:
            raise AssertionError("unexpected extra request: " + method + " " + target)
        item = self.responses.pop(0)
        # 队列项是异常时按传输失败抛出，用来模拟分块传输中断。
        if isinstance(item, Exception):
            raise item
        status, raw = item
        return int(status), target, raw

def request_form_fields(call):
    """把一次 POST 的正文解析回表单字段，便于断言传了哪些参数。

    保留空值字段：11 个过滤字段就是要断言被真的传成了空串。
    """
    text = call["body"].decode("utf-8")
    return {key: values[0] for key, values in parse_qs(text, keep_blank_values=True).items()}


def truncated_transfer():
    """造一次分块传输中断：正文读到一半就断。"""
    return http.client.IncompleteRead(b"<html>", 1024)


def truncated_body():
    """造一份没传完的课表正文：有表头，但表格没有闭合标签。"""
    body = classroom_table([data_row("数学楼401", {0})])
    return "200", body[: body.index("</table>")]


def dictionary_body(items):
    """拼一份 queryJs2 响应。"""
    return "200", json.dumps({"result": True, "list": list(items)})


class ClassroomRequestTestCase(ClassroomCacheTestCase):
    """请求层用例的共同部分：临时缓存目录、假客户端与进程内刷新记录。"""

    def setUp(self):
        """先按缓存用例准备好临时目录，再清掉本进程已经等到的刷新结果。"""
        super().setUp()
        reset_serial_refresh()

    def fake_client(self, *responses):
        """取一个假客户端：响应按顺序给出，用来替代真实会话。"""
        return FakeClassroomClient(responses)

    def table_response(self, rooms=(("数学楼401", {0}),)):
        """拼一份可用的课表响应：一行教室名加占用下标。"""
        rows = [data_row(name, occupied) for name, occupied in rooms]
        return "200", classroom_table(rows)


class ClassroomRequestFormTest(ClassroomRequestTestCase):
    """任务 8.1 / 需求 1.5、3.1、3.3、3.4、7.5：请求体字段、路径与请求头。"""

    def query_params(self, **overrides):
        """先过参数校验取参数，再用它拼请求体，保证断言的是真实链路。"""
        values = {
            "semester": SELECTED_SEMESTER,
            "week_start": "6",
            "week_end": "9",
            "weekday": "3",
            "period_start": "1",
            "period_end": "5",
            "keyword": "",
        }
        values.update(overrides)
        params, error = validate_query(
            values["semester"],
            values["week_start"],
            values["week_end"],
            values["weekday"],
            values["period_start"],
            values["period_end"],
            values["keyword"],
        )
        self.assertIsNone(error)
        return params

    def query_form_of(self, params, skjs=""):
        """按校验后的参数拼本次查询的请求体。"""
        return query_form(
            params["semester"],
            PARENT_MODE_ID,
            params["week_start"],
            params["week_end"],
            params["weekday"],
            skjs,
        )

    def test_query_form_carries_week_range_and_the_same_weekday(self):
        """起止周次写进 zc1/zc2，星期起止写同一个值，节次参数与 skjsid 为空。"""
        form = self.query_form_of(self.query_params())
        self.assertEqual(form["zc1"], "6")
        self.assertEqual(form["zc2"], "9")
        self.assertEqual(form["skxq1"], "3")
        self.assertEqual(form["skxq2"], "3")
        self.assertEqual(form["skxq1"], form["skxq2"])
        self.assertEqual(form["jc1"], "")
        self.assertEqual(form["jc2"], "")
        self.assertEqual(form["skjsid"], "")

    def test_query_form_repeats_the_start_week_when_end_is_omitted(self):
        """只给起始周次时结束周次等于它，zc2 跟着 zc1 走。"""
        params = self.query_params(week_end=None)
        self.assertEqual(params["week_end"], params["week_start"])
        form = self.query_form_of(params)
        self.assertEqual(form["zc1"], "6")
        self.assertEqual(form["zc2"], "6")

    def test_semester_form_keeps_the_eleven_time_and_room_fields_empty(self):
        """学期教室名 POST 的 11 个字段全是空串，只有学期与节次模式有值。"""
        form = semester_form(SELECTED_SEMESTER, PARENT_MODE_ID)
        for field in EMPTY_SEMESTER_FIELDS:
            self.assertEqual(form[field], "")
        self.assertEqual(form["xnxqh"], SELECTED_SEMESTER)
        self.assertEqual(form["kbjcmsid"], PARENT_MODE_ID)
        self.assertEqual(set(form), set(EMPTY_SEMESTER_FIELDS) | {"xnxqh", "kbjcmsid"})

    def test_dictionary_form_asks_for_the_full_roster(self):
        """字典 POST 固定空 skjs 与 maxRow=5000，不拿 skjsid 当过滤条件。"""
        self.assertEqual(dictionary_form(), {"skjs": "", "maxRow": "5000"})
        self.assertEqual(DICTIONARY_MAX_ROW, 5000)

    def test_query_request_sends_form_encoding_referer_and_desktop_ua(self):
        """本次查询是一条表单编码的 POST，带父页 Referer 与教室请求专用 UA。"""
        client = self.fake_client(self.table_response())
        parsed, error = fetch_query_page(client, SELECTED_SEMESTER, PARENT_MODE_ID, "6", "9", 3)
        self.assertIsNone(error)
        self.assertIsNotNone(parsed)
        call = client.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["url"], CLASSROOM_IFR_URL)
        self.assertEqual(call["headers"]["Content-Type"], "application/x-www-form-urlencoded")
        self.assertEqual(call["headers"]["Referer"], CLASSROOM_PAGE_URL)
        self.assertEqual(call["headers"]["User-Agent"], CLASSROOM_USER_AGENT)
        fields = request_form_fields(call)
        self.assertEqual(fields["zc1"], "6")
        self.assertEqual(fields["zc2"], "9")
        self.assertEqual(fields["skxq1"], "3")
        self.assertEqual(fields["skxq2"], "3")
        self.assertEqual(fields["jc1"], "")
        self.assertEqual(fields["jc2"], "")
        self.assertEqual(fields["skjsid"], "")

    def test_parent_page_request_is_a_get_with_the_sidebar_referer(self):
        """父页是 GET：带教务侧栏 Referer，不带表单编码，也没有正文。"""
        client = self.fake_client(("200", parent_page()))
        page, error = fetch_parent_page(client)
        self.assertIsNone(error)
        self.assertEqual(page["kbjcmsid"], PARENT_MODE_ID)
        call = client.calls[0]
        self.assertEqual(call["method"], "GET")
        self.assertEqual(call["url"], CLASSROOM_PAGE_URL)
        self.assertEqual(call["headers"]["Referer"], MAIN_URL)
        self.assertEqual(call["headers"]["User-Agent"], CLASSROOM_USER_AGENT)
        self.assertNotIn("Content-Type", call["headers"])
        self.assertIsNone(call["body"])

    def test_dictionary_request_sends_empty_skjs_and_max_row(self):
        """字典 POST 的表单只有空 skjs 与 maxRow，Referer 是父页。"""
        client = self.fake_client(dictionary_body([{"jsid": "JSID-A", "jsmc": "数学楼401"}]))
        dictionary, error = fetch_dictionary(client)
        self.assertIsNone(error)
        self.assertEqual(dictionary["records"][0]["jsmc"], "数学楼401")
        call = client.calls[0]
        self.assertEqual(call["url"], CLASSROOM_DICTIONARY_URL)
        self.assertEqual(call["headers"]["Referer"], CLASSROOM_PAGE_URL)
        fields = request_form_fields(call)
        self.assertEqual(fields["skjs"], "")
        self.assertEqual(fields["maxRow"], "5000")

    def test_semester_request_keeps_every_filter_field_empty(self):
        """学期教室名请求里 11 个过滤字段真发成空串，只有学期与节次模式有值。"""
        client = self.fake_client(self.table_response())
        payload, error = fetch_semester_page(client, SELECTED_SEMESTER, PARENT_MODE_ID)
        self.assertIsNone(error)
        self.assertEqual(payload["rooms"], ["数学楼401"])
        fields = request_form_fields(client.calls[0])
        for field in EMPTY_SEMESTER_FIELDS:
            self.assertEqual(fields[field], "")
        self.assertEqual(fields["xnxqh"], SELECTED_SEMESTER)
        self.assertEqual(fields["kbjcmsid"], PARENT_MODE_ID)

    def test_three_urls_stay_on_the_student_side(self):
        """三条请求都在 kbcx 路径下，没有 kbxx 路径段，也没有教师端 jsjy_ 前缀。"""
        for url in (CLASSROOM_PAGE_URL, CLASSROOM_DICTIONARY_URL, CLASSROOM_IFR_URL):
            self.assertTrue(url.startswith(JWXT_BASE + "/jsxsd/"))
            segments = urlparse(url).path.split("/")
            self.assertIn(STUDENT_PATH_SEGMENT, segments)
            for foreign in FOREIGN_PATH_SEGMENTS:
                self.assertNotIn(foreign, segments)
            for segment in segments:
                self.assertFalse(segment.startswith("jsjy_"))
            self.assertNotIn("jsjy_", url)

    def test_exact_keyword_writes_the_unexpanded_name_into_skjs(self):
        """关键词与未展开 jsmc 完全相同时写该原名，首尾空白不影响匹配。"""
        self.assertEqual(keyword_skjs("数学楼401", KEYWORD_RECORDS), "数学楼401")
        self.assertEqual(keyword_skjs("数学楼401、403", KEYWORD_RECORDS), "数学楼401、403")
        self.assertEqual(keyword_skjs(" 数学楼401 ", KEYWORD_RECORDS), "数学楼401")

    def test_building_or_unknown_keyword_leaves_skjs_empty(self):
        """楼名与无关词都是包含匹配，skjs 留空，只在本地按展示名过滤。"""
        self.assertEqual(keyword_skjs("数学楼", KEYWORD_RECORDS), "")
        self.assertEqual(keyword_skjs("格物楼B102", KEYWORD_RECORDS), "")
        self.assertEqual(keyword_skjs("体育场", KEYWORD_RECORDS), "")
        self.assertEqual(keyword_skjs("", KEYWORD_RECORDS), "")
        self.assertEqual(keyword_skjs("数学楼401", ()), "")

    def test_exact_keyword_flows_into_the_query_request(self):
        """精确教室名查询时发出去的 skjs 就是那条未展开原名。"""
        client = self.fake_client(self.table_response())
        fetch_query_page(
            client,
            SELECTED_SEMESTER,
            PARENT_MODE_ID,
            "6",
            "9",
            3,
            keyword_skjs("数学楼401、403", KEYWORD_RECORDS),
        )
        fields = request_form_fields(client.calls[0])
        self.assertEqual(fields["skjs"], "数学楼401、403")


class ClassroomRequestInvariantPropertyTest(ClassroomRequestTestCase):
    """任务 8.2 / 设计 Correctness Properties 第 6 条 / 需求 1.5、3.1：请求不变式。"""

    # 固定种子让属性测试可复现。
    SEED = 20261013

    # 轮数，覆盖多组周次、星期、大节与关键词。
    ROUNDS = 30

    # 关键词池：空关键词、精确名、楼名与无关词。
    KEYWORD_POOL = ("", "数学楼", "数学楼401", "格物楼B101", "体育场")

    def random_params(self, generator):
        """随机造一组合法参数：周次与星期在范围内，大节起点取块首。"""
        week_start = generator.randint(1, 30)
        week_end = generator.randint(week_start, 30)
        period_start = generator.choice(BLOCK_START_BOUNDS)
        period_end = generator.randint(period_start, 12)
        params, error = validate_query(
            SELECTED_SEMESTER,
            str(week_start),
            str(week_end),
            str(generator.randint(1, 7)),
            str(period_start),
            str(period_end),
            generator.choice(self.KEYWORD_POOL),
        )
        self.assertIsNone(error)
        return params

    def skjs_of(self, params):
        """按参数里的关键词取这次要传的 skjs。"""
        return keyword_skjs(params["keyword"], KEYWORD_RECORDS)

    def test_every_query_form_keeps_jc1_and_jc2_empty(self):
        """随机参数下每次课表表单的 jc1 与 jc2 都是空字符串，星期起止同值。"""
        generator = random.Random(self.SEED)
        for _round in range(self.ROUNDS):
            params = self.random_params(generator)
            form = query_form(
                params["semester"],
                PARENT_MODE_ID,
                params["week_start"],
                params["week_end"],
                params["weekday"],
                self.skjs_of(params),
            )
            self.assertEqual(form["jc1"], "")
            self.assertEqual(form["jc2"], "")
            self.assertEqual(form["zc1"], str(params["week_start"]))
            self.assertEqual(form["zc2"], str(params["week_end"]))
            self.assertEqual(form["skxq1"], str(params["weekday"]))
            self.assertEqual(form["skxq2"], str(params["weekday"]))

    def test_sent_query_request_keeps_jc_empty_and_the_grid_at_35_cells(self):
        """发出去的请求 jc1/jc2 为空，响应网格仍是 7 天 × 5 块共 35 格。"""
        generator = random.Random(self.SEED)
        for _round in range(self.ROUNDS):
            params = self.random_params(generator)
            client = self.fake_client(self.table_response())
            parsed, error = fetch_query_page(
                client,
                params["semester"],
                PARENT_MODE_ID,
                params["week_start"],
                params["week_end"],
                params["weekday"],
                self.skjs_of(params),
            )
            self.assertIsNone(error)
            fields = request_form_fields(client.calls[0])
            self.assertEqual(fields["jc1"], "")
            self.assertEqual(fields["jc2"], "")
            row = parsed["rows"][0]
            self.assertEqual(sorted(row["occupancy"]), list(range(1, 8)))
            for weekday in row["occupancy"]:
                self.assertEqual(len(row["occupancy"][weekday]), len(PERIOD_BLOCKS))


class ClassroomKeywordSkjsPropertyTest(unittest.TestCase):
    """任务 8.3 / 设计 Correctness Properties 第 10 条 / 需求 3.3、3.4、7.5。"""

    # 固定种子让属性测试可复现。
    SEED = 20261014

    # 轮数，覆盖精确名、楼名、展开后单体名与无关词。
    ROUNDS = 40

    # 房号池，用来拼字典记录与关键词。
    NUMBER_POOL = (101, 103, 201, 305, 401, 403, 505, 708)

    # 无关词池：字典里不存在，skjs 必须留空。
    UNRELATED_POOL = ("体育场", "图书馆", "食堂")

    def random_records(self, generator, size=3):
        """随机造几条字典记录：一半是单体名，一半是合称名。"""
        records = []
        for index in range(size):
            building = generator.choice(BUILDING_POOL)
            name = building + str(generator.choice(self.NUMBER_POOL))
            # 一半的记录拼成合称，展开后有两间教室，但 jsmc 仍是合称名。
            if generator.random() < 0.5:
                separator = generator.choice(("、", "."))
                name = name + separator + building + str(generator.choice(self.NUMBER_POOL))
            records.append({"jsid": "JSID-" + str(index), "jsmc": name})
        return records

    def keyword_candidate(self, generator, records):
        """随机取一个关键词：精确名、楼名、展开后单体名或无关词。"""
        kind = generator.randint(0, 3)
        # 精确名：直接用某条记录的未展开 jsmc。
        if kind == 0:
            return generator.choice(records)["jsmc"]
        # 楼名：只取前缀，属于包含匹配，不该写进 skjs。
        if kind == 1:
            return generator.choice(BUILDING_POOL)
        # 展开后的单体名：合称展开出来的一间，通常不是任何一条未展开 jsmc。
        if kind == 2:
            return generator.choice(expand_room_name(generator.choice(records)["jsmc"])["rooms"])
        # 无关词：字典里不存在。
        return generator.choice(self.UNRELATED_POOL)

    def expected_skjs(self, keyword, records):
        """按规则算应有的 skjs：只有与某条未展开 jsmc 完全相同才写该原名。"""
        text = normalize_room_name(keyword)
        for record in records:
            # 规范化后完全相同才算精确教室名。
            if text and normalize_room_name(record["jsmc"]) == text:
                return record["jsmc"]
        return ""

    def test_only_an_unexpanded_jsmc_match_is_written_into_skjs(self):
        """随机关键词下只有与未展开 jsmc 完全相同的才写 skjs，且写的是原名。"""
        generator = random.Random(self.SEED)
        for _round in range(self.ROUNDS):
            records = self.random_records(generator)
            keyword = self.keyword_candidate(generator, records)
            written = keyword_skjs(keyword, records)
            self.assertEqual(written, self.expected_skjs(keyword, records))
            # 写入的必须是字典里那条原文，不是展开后的单体教室名。
            if written:
                self.assertIn(written, [record["jsmc"] for record in records])
                self.assertEqual(normalize_room_name(written), normalize_room_name(keyword))


class ClassroomFetchRetryTest(ClassroomRequestTestCase):
    """任务 8.4 / 需求 2.6 / 设计 Error Handling「分块传输中断」：丢弃正文，最多再请求 2 次。"""

    def test_truncated_transfer_is_retried_and_then_succeeds(self):
        """第一次传输被掐断时丢掉这次正文，第二次拿到完整课表才写缓存。"""
        client = self.fake_client(truncated_transfer(), self.table_response())
        payload, error = fetch_semester_page(client, SELECTED_SEMESTER, PARENT_MODE_ID)
        self.assertIsNone(error)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(payload["rooms"], ["数学楼401"])
        self.assertTrue(os.path.exists(semester_cache_path(SELECTED_SEMESTER)))

    def test_three_truncated_transfers_fail_without_writing_cache(self):
        """三次都被掐断时该学期刷新失败，残缺正文一个字都不写进缓存。"""
        client = self.fake_client(
            truncated_transfer(), truncated_transfer(), truncated_transfer()
        )
        payload, error = fetch_semester_page(client, SELECTED_SEMESTER, PARENT_MODE_ID)
        self.assertIsNone(payload)
        self.assertFalse(error["ok"])
        self.assertIn("传输中断", error["error"])
        self.assertIn("已请求 3 次", error["error"])
        self.assertEqual(FETCH_ATTEMPTS, 3)
        self.assertEqual(len(client.calls), FETCH_ATTEMPTS)
        self.assertIsNone(read_semester_cache(SELECTED_SEMESTER))
        self.assertFalse(os.path.exists(semester_cache_path(SELECTED_SEMESTER)))

    def test_unfinished_body_is_discarded_then_retried(self):
        """正文没传完（缺闭合表格）时同样丢弃重试，成功那次才写缓存。"""
        client = self.fake_client(truncated_body(), self.table_response())
        payload, error = fetch_semester_page(client, SELECTED_SEMESTER, PARENT_MODE_ID)
        self.assertIsNone(error)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(payload["rooms"], ["数学楼401"])

    def test_three_unfinished_bodies_fail_without_writing_cache(self):
        """三次都只拿到残缺正文时按刷新失败处理，缓存里没有这个学期。"""
        client = self.fake_client(truncated_body(), truncated_body(), truncated_body())
        payload, error = fetch_semester_page(client, SELECTED_SEMESTER, PARENT_MODE_ID)
        self.assertIsNone(payload)
        self.assertFalse(error["ok"])
        self.assertEqual(len(client.calls), FETCH_ATTEMPTS)
        self.assertIsNone(read_semester_cache(SELECTED_SEMESTER))

    def test_login_page_is_not_retried(self):
        """登录页是完整页面，重试只会拿到同一份，因此只请求一次就停。"""
        client = self.fake_client(("200", parent_page(title="登录", body="请输入密码")))
        payload, error = fetch_semester_page(client, SELECTED_SEMESTER, PARENT_MODE_ID)
        self.assertIsNone(payload)
        self.assertIn("重新登录", error["hint"])
        self.assertEqual(len(client.calls), 1)

    def test_session_kicked_page_is_not_retried(self):
        """会话互踢提示同样是完整页面，一次请求就停，且不写缓存。"""
        body = "<html><body>" + SESSION_KICKED_TEXT + "</body></html>"
        client = self.fake_client(("200", body))
        payload, error = fetch_semester_page(client, SELECTED_SEMESTER, PARENT_MODE_ID)
        self.assertIsNone(payload)
        self.assertIn("会话互踢", error["error"])
        self.assertEqual(len(client.calls), 1)
        self.assertIsNone(read_semester_cache(SELECTED_SEMESTER))

    def test_query_page_never_writes_the_semester_cache(self):
        """带周次与星期的课表是按时间过滤过的行，不进学期缓存。"""
        client = self.fake_client(self.table_response())
        parsed, error = fetch_query_page(client, SELECTED_SEMESTER, PARENT_MODE_ID, "6", "9", 3)
        self.assertIsNone(error)
        self.assertEqual(len(parsed["rows"]), 1)
        self.assertIsNone(read_semester_cache(SELECTED_SEMESTER))
        self.assertFalse(os.path.exists(semester_cache_path(SELECTED_SEMESTER)))

    def test_parent_page_truncated_body_is_retried(self):
        """父页标题取不出来（正文没传完）时重试，第二次拿到完整父页。"""
        client = self.fake_client(("200", "<html><head><title>"), ("200", parent_page()))
        page, error = fetch_parent_page(client)
        self.assertIsNone(error)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(page["selected"], SELECTED_SEMESTER)

    def test_truncated_dictionary_json_is_retried(self):
        """字典正文被掐断（JSON 读不动）时重试一次，第二次拿到完整名单。"""
        client = self.fake_client(("200", '{"result": tr'), dictionary_body([{"jsid": "JSID-A", "jsmc": "数学楼401"}]))
        dictionary, error = fetch_dictionary(client)
        self.assertIsNone(error)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(dictionary["records"][0]["jsmc"], "数学楼401")

    def test_truncated_roster_is_not_retried(self):
        """list 长度等于 maxRow 是服务端给出的截断结论，重试只会拿到同样的长度。"""
        items = [{"jsid": "JSID-" + str(index), "jsmc": "数学楼" + str(index)} for index in range(DICTIONARY_MAX_ROW)]
        client = self.fake_client(dictionary_body(items))
        dictionary, error = fetch_dictionary(client)
        self.assertIsNone(dictionary)
        self.assertIn("截断", error["error"])
        self.assertEqual(len(client.calls), 1)


class ClassroomSerialRefreshTest(ClassroomRequestTestCase):
    """需求 2.5：同一进程同一资源只刷新一次，后来的调用等同一次结果。"""

    def test_same_resource_name_shares_one_lock(self):
        """同一资源名拿到同一个锁对象，不同资源各有各的锁。"""
        self.assertIs(serial_lock("semester:2026-2027-1"), serial_lock("semester:2026-2027-1"))
        self.assertIsNot(serial_lock("semester:2026-2027-1"), serial_lock("semester:2026-2027-2"))

    def test_concurrent_calls_to_the_same_semester_request_once(self):
        """同一学期的并发调用只发一次请求，每个调用者拿到的是同一次结果。"""
        client = self.fake_client(self.table_response())
        results = []
        results_guard = threading.Lock()

        def worker():
            """一个调用者：拉同一个学期并记下结果。"""
            payload, error = fetch_semester_page(client, SELECTED_SEMESTER, PARENT_MODE_ID)
            with results_guard:
                results.append((payload, error))

        threads = [threading.Thread(target=worker) for _index in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(results), 6)
        self.assertEqual([error for _payload, error in results], [None] * 6)
        self.assertEqual(len(client.calls), 1)
        self.assertTrue(all(payload is results[0][0] for payload, _error in results))

    def test_different_semesters_are_refreshed_separately(self):
        """不同学期各自请求一次，不共用同一次结果。"""
        client = self.fake_client(self.table_response(), self.table_response())
        first, first_error = fetch_semester_page(client, "2026-2027-1", PARENT_MODE_ID)
        second, second_error = fetch_semester_page(client, "2026-2027-2", PARENT_MODE_ID)
        self.assertIsNone(first_error)
        self.assertIsNone(second_error)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(first["semester"], "2026-2027-1")
        self.assertEqual(second["semester"], "2026-2027-2")

    def test_failed_refresh_is_reused_within_the_process(self):
        """刷新失败的结果同样复用：同一进程不会再对同一学期重发请求。"""
        client = self.fake_client(
            truncated_transfer(), truncated_transfer(), truncated_transfer()
        )
        _payload, error = fetch_semester_page(client, SELECTED_SEMESTER, PARENT_MODE_ID)
        self.assertIn("传输中断", error["error"])
        again_payload, again_error = fetch_semester_page(client, SELECTED_SEMESTER, PARENT_MODE_ID)
        self.assertIsNone(again_payload)
        self.assertEqual(again_error, error)
        self.assertEqual(len(client.calls), FETCH_ATTEMPTS)

    def test_reset_lets_the_next_query_refresh_again(self):
        """清掉进程内记录后，下一次查询会重新刷新这个资源。"""
        client = self.fake_client(self.table_response(), self.table_response())
        fetch_semester_page(client, SELECTED_SEMESTER, PARENT_MODE_ID)
        reset_serial_refresh()
        fetch_semester_page(client, SELECTED_SEMESTER, PARENT_MODE_ID)
        self.assertEqual(len(client.calls), 2)

    def test_page_and_dictionary_have_their_own_resources(self):
        """父页与字典各自一个资源名：第二次调用复用结果，不再请求上游。"""
        page_body = parent_page()
        dictionary_data = dictionary_body([{"jsid": "JSID-A", "jsmc": "数学楼401"}])
        client = self.fake_client(("200", page_body), dictionary_data)
        fetch_parent_page(client)
        fetch_dictionary(client)
        page, page_error = fetch_parent_page(client)
        dictionary, dictionary_error = fetch_dictionary(client)
        self.assertIsNone(page_error)
        self.assertIsNone(dictionary_error)
        self.assertEqual(page["selected"], SELECTED_SEMESTER)
        self.assertEqual(dictionary["records"][0]["jsid"], "JSID-A")
        self.assertEqual(len(client.calls), 2)


# 反推用例：字典、本学年学期教室名与本次课表都是拼出来的纯数据，不发请求、不读真实缓存。


# 反推用例里的楼栋前缀：房号决定是不是同一间教室。
REVERSE_BUILDING = "格物楼"

# 本学年秋季（目标学期）的教室名范围：起点与间数，够 200 间阈值。
REVERSE_AUTUMN_RANGE = (101, 200)

# 本学年春季的教室名范围：另外一段房号，用来覆盖「另一个完整学期有排课」。
REVERSE_SPRING_RANGE = (301, 250)


def reverse_room(number):
    """拼一间教室的展示名：楼栋前缀加房号，能展开出单体教室。"""
    return REVERSE_BUILDING + str(number)


def semester_room_names(start, count):
    """拼一个学期连续编号的教室名列表，用来凑够完整阈值。"""
    return [reverse_room(number) for number in range(start, start + count)]


def block_cell_index(weekday, block_name):
    """算某个「星期 × 大节」在 35 格里的下标：(星期 - 1) × 5 + 块序号。"""
    names = [name for name, _periods in PERIOD_BLOCKS]
    return (weekday - 1) * len(PERIOD_BLOCKS) + names.index(block_name)


class ClassroomReverseTestCase(ClassroomCacheTestCase):
    """反推用例的共同部分：拼字典、本学年学期教室名、本次课表与查询参数。"""

    # 属性测试的固定种子与轮数，让用例可复现。
    SEED = 20261018
    ROUNDS = 20

    # 全部 5 个大节，按表头顺序。
    ALL_BLOCKS = tuple(name for name, _periods in PERIOD_BLOCKS)

    def dictionary_of_rooms(self, names):
        """把教室名列表拼成字典：一条记录一间教室，jsid 按顺序编号。"""
        items = [{"jsid": "JSID-" + str(index), "jsmc": name} for index, name in enumerate(names)]
        return self.dictionary_of(items)

    def params_of(self, **overrides):
        """拼一份通过校验的查询参数；默认查第 6 周星期三的 1–2 节。"""
        values = {
            "semester": SELECTED_SEMESTER,
            "week_start": 6,
            "week_end": 6,
            "weekday": 3,
            "period_start": 1,
            "period_end": 2,
            "keyword": "",
        }
        values.update(overrides)
        params, error = validate_query(
            values["semester"],
            values["week_start"],
            values["week_end"],
            values["weekday"],
            values["period_start"],
            values["period_end"],
            values["keyword"],
        )
        self.assertIsNone(error)
        return params

    def result_names(self, dictionary, semesters, parsed, params, keyword):
        """按给定关键词跑一次反推，返回结果里的教室名列表。"""
        picked = dict(params)
        picked["keyword"] = normalize_room_name(keyword)
        result = empty_classroom_result(dictionary, semesters, parsed, picked)
        self.assertTrue(result["ok"])
        return [room["name"] for room in result["rooms"]]

    def random_scene(self, generator):
        """随机造一份场景：返回 (字典, 学期教室名, 本次课表, 查询参数)。

        字典里有秋季名单里的教室、只出现在春季名单里的教室，以及本学年哪个完整学期都没有的
        教室；秋季与春季都凑够阈值；本次课表随机覆盖字典教室的一部分行。
        """
        autumn_only = generator.sample(range(101, 125), generator.randint(0, 4))
        spring_only = generator.sample(range(301, 325), generator.randint(1, 4))
        idle = generator.sample(range(601, 621), generator.randint(1, 3))
        names = [reverse_room(number) for number in sorted(autumn_only + spring_only + idle)]
        semesters = {
            SELECTED_SEMESTER: semester_room_names(*REVERSE_AUTUMN_RANGE),
            "2026-2027-2": semester_room_names(*REVERSE_SPRING_RANGE),
        }
        rows = []
        for name in generator.sample(names, generator.randint(1, len(names))):
            # 每行随机放 0 到 3 个课程格，覆盖各个星期与块。
            rows.append((name, {generator.randrange(GRID_CELL_COUNT) for _index in range(generator.randint(0, 3))}))
        start = generator.choice(BLOCK_START_BOUNDS)
        params = self.params_of(
            weekday=generator.randint(1, 7),
            period_start=start,
            period_end=generator.randint(start, 12),
        )
        return self.dictionary_of_rooms(names), semesters, self.parsed_table(rows), params

class ClassroomReverseUnitTest(ClassroomReverseTestCase):
    """任务 9.1 / 需求 3.5、3.6、3.7、3.8、4.1、4.2、5.2：候选减占用、空闲信息与结果组装。"""

    def test_weekday_name_uses_the_chinese_label(self):
        """对外展示的星期名用「星期三」这类写法，不归一成「周三」。"""
        name = reverse_room(101)
        semesters = {SELECTED_SEMESTER: semester_room_names(*REVERSE_AUTUMN_RANGE)}
        parsed = self.parsed_table([(name, {0})])
        result = empty_classroom_result(self.dictionary_of_rooms([name]), semesters, parsed, self.params_of(weekday=3))
        self.assertEqual(result["weekday_name"], "星期三")

    def target_rooms(self):
        """目标学期（秋季）的教室名列表，够完整阈值。"""
        return semester_room_names(*REVERSE_AUTUMN_RANGE)

    def test_candidates_minus_occupied(self):
        """占用集中的教室不进结果，其余候选教室进结果。"""
        rooms = self.target_rooms()
        target = rooms[:3]
        dictionary = self.dictionary_of_rooms(target)
        # 第 1 间在查询日的大节上有课，后两间这周这天没有行。
        parsed = self.parsed_table([(target[0], {block_cell_index(3, "0102")})])
        result = empty_classroom_result(dictionary, {SELECTED_SEMESTER: rooms}, parsed, self.params_of())
        self.assertTrue(result["ok"])
        self.assertEqual([room["name"] for room in result["rooms"]], target[1:])
        self.assertEqual(result["count"], 2)

    def test_row_with_other_blocks_is_not_occupied_and_stays_in_results(self):
        """同行别的块有内容、所选大节为空时，该教室算不上课并出现在结果里。"""
        name = reverse_room(101)
        rooms = self.target_rooms()
        dictionary = self.dictionary_of_rooms([name])
        parsed = self.parsed_table([(name, {block_cell_index(3, "0607")})])
        result = empty_classroom_result(dictionary, {SELECTED_SEMESTER: rooms}, parsed, self.params_of())
        self.assertEqual([room["name"] for room in result["rooms"]], [name])
        room = result["rooms"][0]
        self.assertEqual(room["occupied_blocks"], ["0607"])
        self.assertFalse(room["free_all_day"])
        # 所选大节是 0102，它在所选大节上仍然空闲。
        self.assertIn("0102", room["free_blocks"])

    def test_missing_row_is_no_class_and_free_all_day(self):
        """目标周次与星期里没有该行时仍是「不上课」，且 5 个块全空闲。"""
        name = reverse_room(101)
        other = reverse_room(102)
        rooms = self.target_rooms()
        dictionary = self.dictionary_of_rooms([name, other])
        # 课表里只有另一间教室的行：目标教室这周这天压根不出现。
        parsed = self.parsed_table([(other, {block_cell_index(3, "0102")})])
        result = empty_classroom_result(dictionary, {SELECTED_SEMESTER: rooms}, parsed, self.params_of())
        self.assertEqual([room["name"] for room in result["rooms"]], [name])
        room = result["rooms"][0]
        self.assertTrue(room["free_all_day"])
        self.assertEqual(room["free_blocks"], list(self.ALL_BLOCKS))
        self.assertEqual(room["occupied_blocks"], [])
        self.assertEqual(room["last_free_period"], 12)

    def test_year_round_idle_rooms_are_excluded(self):
        """本学年完整学期都没出现过的教室被排除，并计入排除数量。"""
        active = reverse_room(101)
        idle = reverse_room(601)
        rooms = self.target_rooms()
        dictionary = self.dictionary_of_rooms([active, idle])
        parsed = self.parsed_table([(reverse_room(999), {block_cell_index(3, "0102")})])
        result = empty_classroom_result(dictionary, {SELECTED_SEMESTER: rooms}, parsed, self.params_of())
        self.assertEqual([room["name"] for room in result["rooms"]], [active])
        self.assertEqual(result["excluded_year_round_idle_count"], 1)

    def test_room_scheduled_in_another_semester_is_kept(self):
        """目标学期没有该行、但本学年另一个完整学期有排课的教室留在候选集里。"""
        autumn_only = reverse_room(101)
        spring_only = reverse_room(305)
        semesters = {
            SELECTED_SEMESTER: semester_room_names(*REVERSE_AUTUMN_RANGE),
            "2026-2027-2": semester_room_names(*REVERSE_SPRING_RANGE),
        }
        dictionary = self.dictionary_of_rooms([autumn_only, spring_only])
        parsed = self.parsed_table([(reverse_room(999), {block_cell_index(3, "0102")})])
        result = empty_classroom_result(dictionary, semesters, parsed, self.params_of())
        self.assertEqual([room["name"] for room in result["rooms"]], [autumn_only, spring_only])
        self.assertEqual(result["excluded_year_round_idle_count"], 0)

    def test_keyword_only_narrows_the_results(self):
        """关键词只缩小结果，不新增候选以外的教室。"""
        rooms = self.target_rooms()
        dictionary = self.dictionary_of_rooms(rooms[:3])
        semesters = {SELECTED_SEMESTER: rooms}
        parsed = self.parsed_table([(reverse_room(999), {block_cell_index(3, "0102")})])
        base = self.result_names(dictionary, semesters, parsed, self.params_of(), "")
        self.assertEqual(base, rooms[:3])
        narrowed = self.result_names(dictionary, semesters, parsed, self.params_of(), "格物楼102")
        self.assertEqual(narrowed, [rooms[1]])
        self.assertLessEqual(set(narrowed), set(base))

    def test_keyword_without_hit_keeps_both_notes(self):
        """关键词没有命中时仍是成功结果，count 为 0 且两句说明都在。"""
        rooms = self.target_rooms()
        dictionary = self.dictionary_of_rooms([rooms[0], reverse_room(601)])
        parsed = self.parsed_table([(rooms[0], {block_cell_index(3, "0102")})])
        result = empty_classroom_result(
            dictionary, {SELECTED_SEMESTER: rooms}, parsed, self.params_of(keyword="体育场")
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["rooms"], [])
        self.assertEqual(result["limitation"], LIMITATION_TEXT)
        self.assertEqual(result["block_note"], BLOCK_NOTE_TEXT)
        # 排除数量是关键词过滤前的全年无课教室数，不随关键词变化。
        self.assertEqual(result["excluded_year_round_idle_count"], 1)

    def test_rooms_merged_from_the_same_display_name_lose_their_jsid(self):
        """同名多条字典记录时 jsid 留空，并在 warnings 里记「同名行已合并」。"""
        rooms = self.target_rooms()
        items = ({"jsid": "AAA", "jsmc": rooms[0]}, {"jsid": "BBB", "jsmc": rooms[0]})
        dictionary = self.dictionary_of(items)
        parsed = self.parsed_table([(reverse_room(999), {block_cell_index(3, "0102")})])
        result = empty_classroom_result(dictionary, {SELECTED_SEMESTER: rooms}, parsed, self.params_of())
        self.assertEqual([room["name"] for room in result["rooms"]], [rooms[0]])
        self.assertEqual(result["rooms"][0]["jsid"], "")
        self.assertEqual(result["rooms"][0]["source_names"], [rooms[0]])
        self.assertIn(MERGED_ROOM_WARNING + rooms[0], result["warnings"])


    def test_unexpandable_names_only_show_up_in_warnings(self):
        """字典与课表里无法展开的原始名称只进 warnings，不成为结果行。"""
        rooms = self.target_rooms()
        items = ({"jsid": "AAA", "jsmc": rooms[0]}, {"jsid": "BBB", "jsmc": "演播厅"})
        dictionary = self.dictionary_of(items)
        # 课表里另有一行首格也取不出房号，它同样只能进警告。
        parsed = self.parsed_table([(rooms[0], {block_cell_index(3, "0102")}), ("走廊", {0})])
        result = empty_classroom_result(dictionary, {SELECTED_SEMESTER: rooms}, parsed, self.params_of())
        # 目标学期里唯一能展开的候选教室这天有课，所以结果为空。
        self.assertEqual(result["rooms"], [])
        self.assertIn("演播厅", " ".join(result["warnings"]))
        self.assertIn("走廊", " ".join(result["warnings"]))
        self.assertEqual(result["count"], 0)


class ClassroomSemesterThresholdTest(ClassroomReverseTestCase):
    """任务 9.2 / 需求 4.3、4.4：完整阈值决定反推与全年证据。"""

    def test_incomplete_autumn_stops_reversing(self):
        """秋季只有 199 间时停止反推，失败结果里没有 rooms。"""
        rooms = semester_room_names(101, 199)
        dictionary = self.dictionary_of_rooms(rooms[:3])
        parsed = self.parsed_table([(rooms[0], {block_cell_index(3, "0102")})])
        result = empty_classroom_result(dictionary, {SELECTED_SEMESTER: rooms}, parsed, self.params_of())
        self.assertFalse(result["ok"])
        self.assertIn("完整阈值", result["error"])
        self.assertTrue(result["hint"])
        self.assertNotIn("rooms", result)

    def test_incomplete_summer_is_not_year_evidence(self):
        """夏季只有 49 间时不参与全年证据，只出现在夏季的教室算全年无课。"""
        summer_room = reverse_room(701)
        autumn = semester_room_names(*REVERSE_AUTUMN_RANGE)
        summer = [summer_room] + semester_room_names(801, 48)
        dictionary = self.dictionary_of_rooms([reverse_room(101), summer_room])
        parsed = self.parsed_table([(reverse_room(999), {block_cell_index(3, "0102")})])
        result = empty_classroom_result(
            dictionary, {SELECTED_SEMESTER: autumn, "2026-2027-3": summer}, parsed, self.params_of()
        )
        self.assertTrue(result["ok"])
        self.assertEqual([room["name"] for room in result["rooms"]], [reverse_room(101)])
        self.assertEqual(result["excluded_year_round_idle_count"], 1)

    def test_complete_summer_room_enters_candidates(self):
        """夏季有 50 间且某教室出现在首格时，该教室进候选集。"""
        summer_room = reverse_room(701)
        autumn = semester_room_names(*REVERSE_AUTUMN_RANGE)
        summer = [summer_room] + semester_room_names(801, 49)
        dictionary = self.dictionary_of_rooms([reverse_room(101), summer_room])
        parsed = self.parsed_table([(reverse_room(999), {block_cell_index(3, "0102")})])
        result = empty_classroom_result(
            dictionary, {SELECTED_SEMESTER: autumn, "2026-2027-3": summer}, parsed, self.params_of()
        )
        self.assertTrue(result["ok"])
        self.assertIn(summer_room, [room["name"] for room in result["rooms"]])
        self.assertEqual(result["excluded_year_round_idle_count"], 0)

    def test_no_complete_autumn_or_spring_stops_reversing(self):
        """本学年只有完整夏季时停止反推，说明全年无课判断缺少可用数据。"""
        summer = semester_room_names(701, 60)
        dictionary = self.dictionary_of_rooms(summer[:3])
        parsed = self.parsed_table([(summer[0], {block_cell_index(3, "0102")})])
        result = empty_classroom_result(
            dictionary, {"2026-2027-3": summer}, parsed, self.params_of(semester="2026-2027-3")
        )
        self.assertFalse(result["ok"])
        self.assertIn("秋季", result["error"])
        self.assertTrue(result["hint"])
        self.assertNotIn("rooms", result)

class ClassroomReverseSubsetPropertyTest(ClassroomReverseTestCase):
    """任务 9.3 / 设计 Correctness Properties 第 1 条 / 需求 3.7、4.1：不上课集是子集。"""

    def test_resting_rooms_stay_inside_selected_and_candidate_rooms(self):
        """不上课集逐轮都落在指定教室集与候选集里。"""
        generator = random.Random(self.SEED)
        for _round in range(self.ROUNDS):
            dictionary, semesters, parsed, params = self.random_scene(generator)
            index = dictionary_room_index(dictionary)
            evidence, _complete = semester_evidence_rooms(semesters)
            candidates = candidate_rooms(set(index), evidence)
            selected = selected_rooms(candidates, params["keyword"])
            result = empty_classroom_result(dictionary, semesters, parsed, params)
            self.assertTrue(result["ok"])
            names = {room["name"] for room in result["rooms"]}
            # 不上课集既是指定教室集的子集，也是候选集的子集，还都在字典全集里。
            self.assertLessEqual(names, selected)
            self.assertLessEqual(names, candidates)
            self.assertLessEqual(candidates, set(index))


class ClassroomOccupiedExclusionPropertyTest(ClassroomReverseTestCase):
    """任务 9.4 / 设计 Correctness Properties 第 2 条 / 需求 3.5、3.7：占用集不进不上课集。"""

    def test_occupied_rooms_never_appear_in_the_resting_list(self):
        """占用集逐轮都与不上课集不相交。"""
        generator = random.Random(self.SEED)
        for _round in range(self.ROUNDS):
            dictionary, semesters, parsed, params = self.random_scene(generator)
            blocks = query_blocks(params["period_start"], params["period_end"])
            busy = occupied_rooms(parsed, params["weekday"], blocks)
            occupancy_map = room_occupancy_map(parsed)
            result = empty_classroom_result(dictionary, semesters, parsed, params)
            names = {room["name"] for room in result["rooms"]}
            # 占用集与不上课集不相交。
            self.assertEqual(busy & names, set())
            for name in busy:
                # 占用集里每间教室的占用大节都与所选大节有交集。
                info = room_free_info(occupancy_map[name], params["weekday"])
                self.assertTrue(set(info["occupied_blocks"]) & set(blocks))


class ClassroomYearRoundIdlePropertyTest(ClassroomReverseTestCase):
    """任务 9.5 / 设计 Correctness Properties 第 3 条 / 需求 4.1、4.2：全年无课集与候选集不相交。"""

    def test_year_round_idle_rooms_never_overlap_candidates(self):
        """全年无课集逐轮都与候选集不相交。"""
        generator = random.Random(self.SEED)
        for _round in range(self.ROUNDS):
            dictionary, semesters, parsed, params = self.random_scene(generator)
            index = dictionary_room_index(dictionary)
            evidence, _complete = semester_evidence_rooms(semesters)
            candidates = candidate_rooms(set(index), evidence)
            idle = year_round_idle_rooms(set(index), candidates)
            # 两个集合不相交，并且一起覆盖字典全集。
            self.assertEqual(idle & candidates, set())
            self.assertEqual(idle | candidates, set(index))
            result = empty_classroom_result(dictionary, semesters, parsed, params)
            self.assertEqual(result["excluded_year_round_idle_count"], len(idle))


class ClassroomBlockMonotonicPropertyTest(ClassroomReverseTestCase):
    """任务 9.6 / 设计 Correctness Properties 第 5 条 / 需求 3.2、3.5：范围越宽占用集越大。"""

    # 三种范围都从第 1 小节起，覆盖面逐级包含。
    RANGES = ((1, 2), (1, 5), (1, 12))

    def sets_of_range(self, dictionary, semesters, parsed, weekday, period_start, period_end):
        """按给定大节范围反推一次，返回 (占用集, 不上课集)。"""
        blocks = query_blocks(period_start, period_end)
        params = self.params_of(weekday=weekday, period_start=period_start, period_end=period_end)
        result = empty_classroom_result(dictionary, semesters, parsed, params)
        return occupied_rooms(parsed, weekday, blocks), {room["name"] for room in result["rooms"]}

    def test_wider_ranges_only_grow_occupied_and_shrink_resting(self):
        """范围逐级变宽时占用集递增、不上课集递减。"""
        generator = random.Random(self.SEED)
        for _round in range(self.ROUNDS):
            dictionary, semesters, parsed, params = self.random_scene(generator)
            weekday = params["weekday"]
            pairs = [self.sets_of_range(dictionary, semesters, parsed, weekday, *pair) for pair in self.RANGES]
            # 逐对比较相邻的范围：后一个的覆盖面更大。
            for index in range(len(pairs) - 1):
                narrower = pairs[index]
                wider = pairs[index + 1]
                # 范围更宽时占用集只增不减，不上课集只减不增。
                self.assertLessEqual(narrower[0], wider[0])
                self.assertGreaterEqual(narrower[1], wider[1])

    def test_a_room_busy_only_in_the_last_block_moves_between_sets(self):
        """只在 101112 有课的教室：查 1–2 与 1–5 时不上课，查 1–12 时被占用。"""
        name = reverse_room(101)
        rooms = semester_room_names(*REVERSE_AUTUMN_RANGE)
        dictionary = self.dictionary_of_rooms([name])
        semesters = {SELECTED_SEMESTER: rooms}
        parsed = self.parsed_table([(name, {block_cell_index(3, "101112")})])
        narrow = self.sets_of_range(dictionary, semesters, parsed, 3, 1, 2)
        middle = self.sets_of_range(dictionary, semesters, parsed, 3, 1, 5)
        wide = self.sets_of_range(dictionary, semesters, parsed, 3, 1, 12)
        self.assertEqual(narrow, (set(), {name}))
        self.assertEqual(middle, (set(), {name}))
        self.assertEqual(wide, ({name}, set()))


class ClassroomFreeBlocksPropertyTest(ClassroomReverseTestCase):
    """任务 9.7 / 设计 Correctness Properties 第 7 条 / 需求 3.8：不上课教室在所选大节上都空闲。"""

    def test_selected_blocks_are_always_free_in_the_results(self):
        """结果里每间教室在所选大节上都空闲。"""
        generator = random.Random(self.SEED)
        for _round in range(self.ROUNDS):
            dictionary, semesters, parsed, params = self.random_scene(generator)
            blocks = query_blocks(params["period_start"], params["period_end"])
            result = empty_classroom_result(dictionary, semesters, parsed, params)
            for room in result["rooms"]:
                self.assertLessEqual(set(blocks), set(room["free_blocks"]))
                self.assertEqual(set(room["occupied_blocks"]) & set(blocks), set())

class ClassroomFreeFieldConsistencyPropertyTest(ClassroomReverseTestCase):
    """任务 9.8 / 设计 Correctness Properties 第 8 条 / 需求 3.8：空闲字段自洽。"""

    def assert_consistent(self, room):
        """断言一间教室的空闲字段自洽：互补、全天空闲与最晚可用节次。"""
        free = room["free_blocks"]
        occupied = room["occupied_blocks"]
        self.assertEqual(set(free) & set(occupied), set())
        self.assertEqual(set(free) | set(occupied), set(self.ALL_BLOCKS))
        self.assertEqual(room["free_all_day"], occupied == [])
        expected = 0
        for name in free:
            for block_name, periods in PERIOD_BLOCKS:
                # 空闲块里的末小节按表头顺序比较，最大的那个就是最晚可用节次。
                if block_name == name:
                    expected = max(expected, periods[-1])
        self.assertEqual(room["last_free_period"], expected)

    def test_free_fields_are_consistent_in_every_result(self):
        """结果里每间教室的空闲字段逐轮自洽。"""
        generator = random.Random(self.SEED)
        for _round in range(self.ROUNDS):
            dictionary, semesters, parsed, params = self.random_scene(generator)
            result = empty_classroom_result(dictionary, semesters, parsed, params)
            for room in result["rooms"]:
                self.assert_consistent(room)

    def test_only_the_last_block_occupied_gives_nine(self):
        """只有 101112 有课时最晚可用节次是 9。"""
        name = reverse_room(101)
        rooms = semester_room_names(*REVERSE_AUTUMN_RANGE)
        dictionary = self.dictionary_of_rooms([name])
        parsed = self.parsed_table([(name, {block_cell_index(3, "101112")})])
        result = empty_classroom_result(dictionary, {SELECTED_SEMESTER: rooms}, parsed, self.params_of())
        self.assertEqual([room["name"] for room in result["rooms"]], [name])
        room = result["rooms"][0]
        self.assertEqual(room["occupied_blocks"], ["101112"])
        self.assertEqual(room["free_blocks"], ["0102", "030405", "0607", "0809"])
        self.assertEqual(room["last_free_period"], 9)
        self.assert_consistent(room)

    def test_fully_occupied_day_gives_zero_and_no_row_gives_twelve(self):
        """整天都有课时最晚可用节次为 0，占用位为空（没有该行）时为 12。"""
        busy = room_free_info(expected_occupancy({0, 1, 2, 3, 4}), 1)
        self.assertEqual(busy["free_blocks"], [])
        self.assertEqual(busy["occupied_blocks"], list(self.ALL_BLOCKS))
        self.assertEqual(busy["last_free_period"], 0)
        self.assertFalse(busy["free_all_day"])
        empty = room_free_info({}, 1)
        self.assertEqual(empty["free_blocks"], list(self.ALL_BLOCKS))
        self.assertEqual(empty["last_free_period"], 12)
        self.assertTrue(empty["free_all_day"])


class ClassroomMissingRowPropertyTest(ClassroomReverseTestCase):
    """任务 9.9 / 设计 Correctness Properties 第 9 条 / 需求 3.7、3.8：整天没课的教室仍在结果里。"""

    def test_rooms_missing_from_the_response_still_appear(self):
        """构造一份少了几间教室的响应，缺的教室照样进结果。"""
        rooms = semester_room_names(*REVERSE_AUTUMN_RANGE)
        target = rooms[:4]
        dictionary = self.dictionary_of_rooms(target)
        # 响应里只有第 1 间有课，后三间这周这天整行都不在。
        parsed = self.parsed_table([(target[0], {block_cell_index(3, "0102")})])
        result = empty_classroom_result(dictionary, {SELECTED_SEMESTER: rooms}, parsed, self.params_of())
        self.assertEqual([room["name"] for room in result["rooms"]], target[1:])
        for room in result["rooms"]:
            self.assertTrue(room["free_all_day"])
            self.assertEqual(room["occupied_blocks"], [])
            self.assertEqual(room["last_free_period"], 12)

    def test_results_are_the_difference_of_selected_and_occupied(self):
        """结果集等于指定教室集减去占用集，与响应里出现了哪些行无关。"""
        generator = random.Random(self.SEED)
        for _round in range(self.ROUNDS):
            dictionary, semesters, parsed, params = self.random_scene(generator)
            blocks = query_blocks(params["period_start"], params["period_end"])
            index = dictionary_room_index(dictionary)
            evidence, _complete = semester_evidence_rooms(semesters)
            candidates = candidate_rooms(set(index), evidence)
            busy = occupied_rooms(parsed, params["weekday"], blocks)
            expected = selected_rooms(candidates, params["keyword"]) - busy
            result = empty_classroom_result(dictionary, semesters, parsed, params)
            self.assertEqual({room["name"] for room in result["rooms"]}, expected)


class ClassroomKeywordPropertyTest(ClassroomReverseTestCase):
    """任务 9.10 / 设计 Correctness Properties 第 12 条 / 需求 3.4：关键词过滤不创造字典外教室。"""

    # 覆盖空关键词、楼名、部分房号、完整教室名与字典里没有的词。
    KEYWORDS = ("", "格物楼", "格物楼10", "格物楼101", "体育场")

    def test_keyword_only_narrows_and_never_creates_rooms(self):
        """关键词只缩小结果，且不会造出字典外的教室。"""
        generator = random.Random(self.SEED)
        for _round in range(self.ROUNDS):
            dictionary, semesters, parsed, params = self.random_scene(generator)
            known = set(dictionary_room_index(dictionary))
            base = set(self.result_names(dictionary, semesters, parsed, params, ""))
            for keyword in self.KEYWORDS:
                names = self.result_names(dictionary, semesters, parsed, params, keyword)
                # 结果只会比不带关键词时更少，且每间教室都在字典全集里，按展示名排序。
                self.assertLessEqual(set(names), base)
                self.assertLessEqual(set(names), known)
                self.assertEqual(names, sorted(names))


class ClassroomStatusNotePropertyTest(ClassroomReverseTestCase):
    """任务 9.11 / 设计 Correctness Properties 第 15 条 / 需求 5.1、5.2、5.5：状态与说明恒定。"""

    def test_every_success_carries_the_same_status_and_notes(self):
        """每次成功结果的字段都满足同一套状态与说明口径。"""
        generator = random.Random(self.SEED)
        for _round in range(self.ROUNDS):
            dictionary, semesters, parsed, params = self.random_scene(generator)
            result = empty_classroom_result(dictionary, semesters, parsed, params)
            self.assertEqual(result["limitation"], LIMITATION_TEXT)
            self.assertEqual(result["block_note"], BLOCK_NOTE_TEXT)
            self.assertEqual(result["count"], len(result["rooms"]))
            for room in result["rooms"]:
                self.assertEqual(room["status"], ROOM_STATUS)
                self.assertEqual(room["status_text"], ROOM_STATUS_TEXT)
            # 「空闲」只能描述没有排课，结果里绝不能出现「可借用」。
            self.assertNotIn("可借用", json.dumps(result, ensure_ascii=False))

    def test_empty_result_still_carries_both_notes(self):
        """查询成功但没有教室落入结果时，两句说明仍在。"""
        rooms = semester_room_names(*REVERSE_AUTUMN_RANGE)
        dictionary = self.dictionary_of_rooms(rooms[:2])
        parsed = self.parsed_table([(reverse_room(999), {block_cell_index(3, "0102")})])
        result = empty_classroom_result(
            dictionary, {SELECTED_SEMESTER: rooms}, parsed, self.params_of(keyword="体育场")
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["limitation"], LIMITATION_TEXT)
        self.assertEqual(result["block_note"], BLOCK_NOTE_TEXT)


class ClassroomFreeSwitchPropertyTest(ClassroomReverseTestCase):
    """任务 9.12 / 设计 Correctness Properties 第 16 条 / 需求 3.11、3.12：空闲开关只过滤不改写。"""

    # 单个开关与组合开关：组合取交集（上午 + 晚上就是两者都空闲）。
    SWITCH_GROUPS = (
        ("free_all_day",),
        ("free_morning",),
        ("free_afternoon",),
        ("free_evening",),
        ("free_morning", "free_evening"),
        ("free_morning", "free_afternoon", "free_evening"),
    )

    def test_switches_only_filter_the_result_set(self):
        """按已打开的开关筛结果，不改写任何字段。"""
        # 四个空闲开关的名字与结果字段同名，任务 12 的 CLI 开关就用这几个名字。
        self.assertEqual(set(FREE_SWITCHES), {"free_all_day", "free_morning", "free_afternoon", "free_evening"})
        generator = random.Random(self.SEED)
        for _round in range(self.ROUNDS):
            dictionary, semesters, parsed, params = self.random_scene(generator)
            before = empty_classroom_result(dictionary, semesters, parsed, params)["rooms"]
            snapshot = json.loads(json.dumps(before, ensure_ascii=False))
            # 一个开关都没打开时结果原样返回。
            self.assertEqual(filter_free_rooms(before, ()), before)
            for switches in self.SWITCH_GROUPS:
                filtered = filter_free_rooms(before, switches)
                kept = [room for room in before if all(room[switch] for switch in switches)]
                # 只按已打开的开关筛结果，同时给多个开关即取交集。
                self.assertEqual(filtered, kept)
                for room in filtered:
                    for switch in switches:
                        self.assertTrue(room[switch])
                # 过滤不改写任何字段：整份数据与过滤前逐字一致。
                self.assertEqual(json.loads(json.dumps(before, ensure_ascii=False)), snapshot)


class ClassroomSectionFieldPropertyTest(ClassroomReverseTestCase):
    """任务 9.13 / 设计 Correctness Properties 第 17 条 / 需求 3.12、6.5：时段字段与空闲大节自洽。"""

    def test_section_fields_follow_the_free_blocks(self):
        """每个结果教室的时段字段都与它的空闲大节一致。"""
        generator = random.Random(self.SEED)
        for _round in range(self.ROUNDS):
            dictionary, semesters, parsed, params = self.random_scene(generator)
            result = empty_classroom_result(dictionary, semesters, parsed, params)
            for room in result["rooms"]:
                free = set(room["free_blocks"])
                # 上午是两个块都空闲，下午是两个块都空闲，晚上只看 101112。
                self.assertEqual(room["free_morning"], {"0102", "030405"} <= free)
                self.assertEqual(room["free_afternoon"], {"0607", "0809"} <= free)
                self.assertEqual(room["free_evening"], "101112" in free)
                all_day = room["free_morning"] and room["free_afternoon"] and room["free_evening"]
                self.assertEqual(room["free_all_day"], all_day)

    def test_afternoon_class_keeps_morning_and_evening_free(self):
        """只有 0607 有课时上午与晚上空闲、下午不空闲。"""
        name = reverse_room(101)
        rooms = semester_room_names(*REVERSE_AUTUMN_RANGE)
        dictionary = self.dictionary_of_rooms([name])
        parsed = self.parsed_table([(name, {block_cell_index(3, "0607")})])
        result = empty_classroom_result(dictionary, {SELECTED_SEMESTER: rooms}, parsed, self.params_of())
        room = result["rooms"][0]
        self.assertEqual(room["occupied_blocks"], ["0607"])
        self.assertTrue(room["free_morning"])
        self.assertTrue(room["free_evening"])
        self.assertFalse(room["free_afternoon"])
        self.assertFalse(room["free_all_day"])
        self.assertEqual(room["last_free_period"], 12)


class ClassroomSingleDayPropertyTest(ClassroomReverseTestCase):
    """任务 9.14 / 设计 Correctness Properties 第 18 条 / 需求 3.13：空闲信息为单天口径。"""

    def test_both_request_weekday_fields_are_the_same_day(self):
        """请求里 skxq1 与 skxq2 恒为同一个星期几值。"""
        generator = random.Random(self.SEED)
        for _round in range(self.ROUNDS):
            weekday = generator.randint(1, 7)
            week_start = generator.randint(1, 30)
            form = query_form(SELECTED_SEMESTER, PARENT_MODE_ID, week_start, week_start, weekday)
            self.assertEqual(form["skxq1"], str(weekday))
            self.assertEqual(form["skxq2"], form["skxq1"])

    def test_occupied_blocks_only_reflect_the_queried_weekday(self):
        """查星期三时只算星期三的格：别的天有课不算占用，别的天为空也不改变判定。"""
        name = reverse_room(101)
        rooms = semester_room_names(*REVERSE_AUTUMN_RANGE)
        dictionary = self.dictionary_of_rooms([name])
        monday_full = {block_cell_index(1, block_name) for block_name, _periods in PERIOD_BLOCKS}
        # 星期一整天有课、星期三没有课：查星期三时这间教室仍然是「不上课」。
        parsed = self.parsed_table([(name, monday_full)])
        params = self.params_of(period_start=1, period_end=12)
        room = empty_classroom_result(dictionary, {SELECTED_SEMESTER: rooms}, parsed, params)["rooms"][0]
        self.assertEqual(room["occupied_blocks"], [])
        self.assertTrue(room["free_all_day"])
        self.assertEqual(room["last_free_period"], 12)
        # 同样的占用位落在星期一就是全天有课，说明两天的格没有被混在一起。
        monday = room_free_info(room_occupancy_map(parsed)[name], 1)
        self.assertEqual(monday["occupied_blocks"], list(self.ALL_BLOCKS))
        # 星期三 101112 有课时只把 101112 算成占用，别的天有没有课都不改这个结论。
        parsed = self.parsed_table([(name, monday_full | {block_cell_index(3, "101112")})])
        wednesday = room_free_info(room_occupancy_map(parsed)[name], 3)
        self.assertEqual(wednesday["occupied_blocks"], ["101112"])
        self.assertEqual(wednesday["last_free_period"], 9)
        self.assertTrue(wednesday["free_morning"])
        self.assertFalse(wednesday["free_evening"])


# 编排用例：响应都拼在假客户端上，缓存目录在临时目录里，绝不发真实请求、不读真实 Cookie。


# 本学年三个学期的教室名范围：秋、春各凑够 200 间阈值，夏季凑够 50 间。
ORCH_AUTUMN_RANGE = (101, 200)
ORCH_SPRING_RANGE = (301, 200)
ORCH_SUMMER_RANGE = (601, 50)
# 只出现在字典里、本学年哪个学期都没有排课的教室：用来断言全年无课计数。
ORCH_IDLE_RANGE = (701, 3)

# 本次查询的固定口径：第 6 周星期三 1–2 节，正好落在 0102 块。
ORCH_WEEK = 6
ORCH_WEEKDAY = 3
ORCH_BLOCK = "0102"
# 本次课表里固定放一行字典外的教室：让响应至少有一行首格能取出教室名，又不影响候选集。
ORCH_SPARE_ROOM = "格物楼999"

# 失败页插入的三个位置：父页 GET、学期课表 POST、本次查询 POST。
PARENT_POSITION = "parent"
SEMESTER_POSITION = "semester"
QUERY_POSITION = "query"

# 登录页：标题写着登录，正文有登录表单标记。
LOGIN_PAGE = "<html><head><title>登录</title></head><body>请输入账号 请输入密码</body></html>"
# 非法访问页：接口路径误写成 kbxx 时会拿到它。
ILLEGAL_ACCESS_PAGE = "<html><body>提示：非法访问！</body></html>"
# 会话互踢页：账号在别处登录，与参数无关。
SESSION_KICKED_PAGE = "<html><body>" + SESSION_KICKED_TEXT + "</body></html>"
# 标题既不是教室课表也不是登录页：父页位置上说明页面未被识别。
OTHER_TITLE_PAGE = "<html><head><title>错误提示</title></head><body>系统忙</body></html>"
# 缺 table#kbtable 的页面：课表 POST 位置拿不到 35 格。
NO_TABLE_PAGE = (
    "<html><head><title>" + PARENT_TITLE + "</title></head>"
    "<body><table id='other'></table></body></html>"
)
# 格数不是 35 的课表：结构变了，不能按缺失的列推断空闲。
SHORT_GRID_PAGE = classroom_table([("格物楼B101", grid_cells({0})[:-1])])

# 失败页样本：(说明, 页面, 不可用的位置, 这次会被请求几次)。
# 登录页、非法访问与互踢都是完整页面，重试也拿不到别的，所以只请求一次；
# 结构坏掉的页面按「正文没传完」重试到次数上限。
FAILURE_PAGE_SAMPLES = (
    ("登录页", LOGIN_PAGE, (PARENT_POSITION, SEMESTER_POSITION, QUERY_POSITION), 1),
    ("非法访问", ILLEGAL_ACCESS_PAGE, (PARENT_POSITION, SEMESTER_POSITION, QUERY_POSITION), 1),
    ("会话互踢", SESSION_KICKED_PAGE, (PARENT_POSITION, SEMESTER_POSITION, QUERY_POSITION), 1),
    ("标题不是教室课表", OTHER_TITLE_PAGE, (PARENT_POSITION,), FETCH_ATTEMPTS),
    ("缺 kbtable", NO_TABLE_PAGE, (SEMESTER_POSITION, QUERY_POSITION), FETCH_ATTEMPTS),
    ("格数不是 35", SHORT_GRID_PAGE, (SEMESTER_POSITION, QUERY_POSITION), FETCH_ATTEMPTS),
)


def orchestration_semester_rooms():
    """本学年三个学期各自的教室名列表，供写学期缓存与算候选集用。"""
    return {
        SELECTED_SEMESTER: semester_room_names(*ORCH_AUTUMN_RANGE),
        "2026-2027-2": semester_room_names(*ORCH_SPRING_RANGE),
        "2026-2027-3": semester_room_names(*ORCH_SUMMER_RANGE),
    }


def orchestration_dictionary_names():
    """字典里的教室名：本学年三个学期的教室加上全年无课的教室。"""
    rooms = orchestration_semester_rooms()
    names = list(semester_room_names(*ORCH_IDLE_RANGE))
    for semester in EXPECTED_YEAR_SEMESTERS:
        names.extend(rooms[semester])
    return sorted(names)


class CacheRefreshingClient(FakeClassroomClient):
    """假客户端：命中某次请求时先写一份缓存，模拟另一个进程在这段时间刷新成功。

    命中判定只看表单字段与取值（字典请求按 maxRow、学期请求按 xnxqh）。编排在刷新失败后会重新
    读一次缓存文件，这个类让那次重读读到未过期的缓存，用来覆盖「刷新失败但有未过期缓存时继续」
    的降级路径。
    """

    def __init__(self, responses, field, value, writer):
        super().__init__(responses)
        self.field = field  # 命中用的表单字段名
        self.value = value  # 命中用的字段取值
        self.writer = writer  # 命中时写缓存的回调，由用例拼好教室名

    def text(self, method, target, body=None, headers=None, same_origin=False):
        """命中时先写缓存文件，再交出这次请求原本的（失败）响应。"""
        fields = request_form_fields({"body": body}) if body else {}
        # 表单字段不等于目标取值时不是要模拟的那次请求，按原样返回。
        if fields.get(self.field) == self.value:
            self.writer()
        return super().text(method, target, body, headers, same_origin)


class ClassroomOrchestrationTestCase(ClassroomRequestTestCase):
    """编排用例的共同部分：临时缓存目录、假客户端与拼好的本学年学期教室名。"""

    def run_query(self, client, **overrides):
        """跑一次编排：默认查 2026-2027-1 第 6 周星期三 1–2 节，返回结果信封。"""
        values = {
            "semester": SELECTED_SEMESTER,
            "week_start": str(ORCH_WEEK),
            "week_end": str(ORCH_WEEK),
            "weekday": str(ORCH_WEEKDAY),
            "period_start": "1",
            "period_end": "2",
            "keyword": "",
        }
        values.update(overrides)
        return query_empty_classrooms(
            client,
            values["semester"],
            values["week_start"],
            values["week_end"],
            values["weekday"],
            values["period_start"],
            values["period_end"],
            values["keyword"],
        )

    def dictionary_items(self, names):
        """把教室名列表拼成字典条目：一条记录一间教室，jsid 按顺序编号。"""
        return [{"jsid": "JSID-" + str(index), "jsmc": name} for index, name in enumerate(names)]

    def prime_dictionary(self, names, fetched_at=None):
        """把字典缓存写好；默认时间戳是当前时刻，也就是未过期。"""
        return write_dictionary_cache(
            self.dictionary_of(self.dictionary_items(names)), fetched_at or now_moment()
        )

    def prime_semester(self, semester, names, fetched_at=None):
        """把某学期的教室名缓存写好；默认时间戳是当前时刻，也就是未过期。"""
        parsed = self.parsed_table([(name, {0}) for name in names])
        return write_semester_cache(semester, PARENT_MODE_ID, parsed, fetched_at or now_moment())

    def prime_year(self):
        """把本学年三个学期的缓存都写成未过期，并按学期返回教室名列表。"""
        rooms = orchestration_semester_rooms()
        for semester in EXPECTED_YEAR_SEMESTERS:
            self.prime_semester(semester, rooms[semester])
        return rooms

    def expired_moment(self):
        """取一个早于 7 日的时刻：用它的缓存算过期，编排会去刷新这个学期或字典。"""
        return now_moment() - timedelta(days=CACHE_TTL_DAYS + 1)

    def semester_response(self, names):
        """拼一份学期课表响应：一行一间教室，占用格落在当天第一格。"""
        return "200", classroom_table([data_row(name, {0}) for name in names])

    def query_response(self, rooms=None):
        """拼一份本次查询的课表响应；不给 rooms 时放一行字典外教室，保证响应可解析。"""
        picked = [(ORCH_SPARE_ROOM, set())] if rooms is None else list(rooms)
        return "200", classroom_table([data_row(name, occupied) for name, occupied in picked])

    def assert_failure(self, result, text, hint_text=""):
        """断言结果是失败、点出原因、不含 rooms，并给出可执行的下一步提示。"""
        self.assertFalse(result["ok"])
        self.assertIn(text, result["error"])
        self.assertTrue(result["hint"])
        self.assertNotIn("rooms", result)
        # 会话类失败必须让用户知道要重新登录，否则提示等于没说。
        if hint_text:
            self.assertIn(hint_text, result["hint"])


class ClassroomOrchestrationTest(ClassroomOrchestrationTestCase):
    """任务 11.1 / 需求 2.2、2.6、8.1、8.5、8.6：编排顺序、缓存降级与会话中断。"""

    def test_success_fills_all_four_cache_fields(self):
        """一次全新查询：缓存缺失时逐个刷新，成功结果按实际来源填 cache 的四个字段。"""
        rooms = orchestration_semester_rooms()
        names = orchestration_dictionary_names()
        occupied = rooms[SELECTED_SEMESTER][0]
        client = self.fake_client(
            ("200", parent_page()),
            dictionary_body(self.dictionary_items(names)),
            self.semester_response(rooms[SELECTED_SEMESTER]),
            self.semester_response(rooms["2026-2027-2"]),
            self.semester_response(rooms["2026-2027-3"]),
            self.query_response([(occupied, {block_cell_index(ORCH_WEEKDAY, ORCH_BLOCK)})]),
        )
        result = self.run_query(client)
        self.assertTrue(result["ok"])
        self.assertEqual(
            result["cache"],
            {
                "semesters": EXPECTED_YEAR_SEMESTERS,
                "refreshed_semesters": EXPECTED_YEAR_SEMESTERS,
                "stale_semesters": [],
                "stale_dictionary": False,
            },
        )
        # 本学年没排过课的教室只给计数，当天有课的那间也要从结果里去掉。
        self.assertEqual(result["excluded_year_round_idle_count"], ORCH_IDLE_RANGE[1])
        self.assertEqual(result["count"], len(names) - ORCH_IDLE_RANGE[1] - 1)
        self.assertNotIn(occupied, [room["name"] for room in result["rooms"]])
        # 本次课表按周次与星期过滤，节次留空才能拿到整天 35 格。
        self.assertEqual(client.calls[-1]["url"], CLASSROOM_IFR_URL)
        form = request_form_fields(client.calls[-1])
        self.assertEqual(form["jc1"], "")
        self.assertEqual(form["jc2"], "")
        self.assertEqual(form["zc1"], str(ORCH_WEEK))
        self.assertEqual(form["zc2"], str(ORCH_WEEK))
        self.assertEqual(form["skxq1"], str(ORCH_WEEKDAY))
        self.assertEqual(form["skxq2"], str(ORCH_WEEKDAY))

    def test_year_without_a_summer_semester_still_succeeds(self):
        """父页下拉里没有夏季时只拉秋与春，仍然能反推成功。"""
        rooms = orchestration_semester_rooms()
        names = orchestration_dictionary_names()
        options = ("2026-2027-2", "2026-2027-1", "2025-2026-1")
        client = self.fake_client(
            ("200", parent_page(values=options)),
            dictionary_body(self.dictionary_items(names)),
            self.semester_response(rooms[SELECTED_SEMESTER]),
            self.semester_response(rooms["2026-2027-2"]),
            self.query_response(),
        )
        result = self.run_query(client)
        self.assertTrue(result["ok"])
        self.assertEqual(result["cache"]["semesters"], ["2026-2027-1", "2026-2027-2"])
        self.assertEqual(result["cache"]["refreshed_semesters"], ["2026-2027-1", "2026-2027-2"])

    def test_unexpired_caches_are_reused_without_extra_posts(self):
        """缓存都在 7 日内时只发本次课表，不再为字典与学期发请求。"""
        self.prime_year()
        self.prime_dictionary(orchestration_dictionary_names())
        client = self.fake_client(("200", parent_page()), self.query_response())
        result = self.run_query(client)
        self.assertTrue(result["ok"])
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[0]["method"], "GET")
        self.assertEqual(client.calls[1]["url"], CLASSROOM_IFR_URL)
        self.assertEqual(result["cache"]["semesters"], EXPECTED_YEAR_SEMESTERS)
        self.assertEqual(result["cache"]["refreshed_semesters"], [])
        self.assertEqual(result["cache"]["stale_semesters"], [])
        self.assertFalse(result["cache"]["stale_dictionary"])

    def test_semester_refresh_failure_keeps_an_unexpired_cache(self):
        """某学期刷新失败、但缓存已被别的进程刷新好时继续，并把该学期记进 stale_semesters。"""
        rooms = orchestration_semester_rooms()
        names = orchestration_dictionary_names()
        spring = "2026-2027-2"
        self.prime_dictionary(names)
        # 秋、夏用未过期缓存；春季缓存过期，会去刷新。
        self.prime_semester(SELECTED_SEMESTER, rooms[SELECTED_SEMESTER])
        self.prime_semester("2026-2027-3", rooms["2026-2027-3"])
        self.prime_semester(spring, rooms[spring], self.expired_moment())

        def writer():
            """模拟另一个进程在这段时间把春季缓存刷新好。"""
            self.prime_semester(spring, rooms[spring])

        client = CacheRefreshingClient(
            [
                ("200", parent_page()),
                truncated_transfer(),
                truncated_transfer(),
                truncated_transfer(),
                self.query_response(),
            ],
            "xnxqh",
            spring,
            writer,
        )
        result = self.run_query(client)
        self.assertTrue(result["ok"])
        self.assertEqual(result["cache"]["stale_semesters"], [spring])
        self.assertEqual(result["cache"]["refreshed_semesters"], [])
        self.assertFalse(result["cache"]["stale_dictionary"])
        # 春季的 200 间教室照样算进候选集，说明用的是那份未过期的缓存。
        self.assertEqual(result["count"], len(names) - ORCH_IDLE_RANGE[1])
        self.assertEqual(len(client.calls), 5)

    def test_truncated_dictionary_list_keeps_an_unexpired_cache(self):
        """字典名单被 maxRow 截断时不用该次响应，未过期的旧字典可以继续并标记 stale_dictionary。"""
        self.prime_year()
        new_names = orchestration_dictionary_names()
        self.prime_dictionary(new_names[:1], self.expired_moment())

        def writer():
            """模拟另一个进程在这段时间把字典刷新好。"""
            self.prime_dictionary(new_names)

        client = CacheRefreshingClient(
            [
                ("200", parent_page()),
                dictionary_body([{"jsid": "JSID-X", "jsmc": "格物楼101"}] * DICTIONARY_MAX_ROW),
                self.query_response(),
            ],
            "maxRow",
            str(DICTIONARY_MAX_ROW),
            writer,
        )
        result = self.run_query(client)
        self.assertTrue(result["ok"])
        self.assertTrue(result["cache"]["stale_dictionary"])
        # 教室全集来自那份未过期的字典：全年无课的教室数只能是新字典里的那几间。
        self.assertEqual(result["excluded_year_round_idle_count"], ORCH_IDLE_RANGE[1])
        self.assertEqual(result["count"], len(new_names) - ORCH_IDLE_RANGE[1])
        self.assertEqual(len(client.calls), 3)

    def test_semester_without_any_usable_cache_stops(self):
        """某学期刷不出来又没有可用缓存时停下，说明全年无课名单不完整。"""
        names = orchestration_dictionary_names()
        client = self.fake_client(
            ("200", parent_page()),
            dictionary_body(self.dictionary_items(names)),
            truncated_transfer(),
            truncated_transfer(),
            truncated_transfer(),
        )
        result = self.run_query(client)
        self.assert_failure(result, "全年无课名单不完整")
        # 秋季 3 次都断线后立刻停下，不再拉春季与夏季，也不发本次课表。
        self.assertEqual(len(client.calls), 5)
        self.assertIsNone(read_semester_cache(SELECTED_SEMESTER))

    def test_expired_semester_cache_is_not_usable(self):
        """某学期只有过期缓存、刷新又失败时停下：过期缓存不算可用。"""
        rooms = orchestration_semester_rooms()
        self.prime_dictionary(orchestration_dictionary_names())
        self.prime_semester(SELECTED_SEMESTER, rooms[SELECTED_SEMESTER], self.expired_moment())
        client = self.fake_client(
            ("200", parent_page()),
            truncated_transfer(),
            truncated_transfer(),
            truncated_transfer(),
        )
        result = self.run_query(client)
        self.assert_failure(result, "全年无课名单不完整")
        # 只发了父页与秋季的 3 次断线请求，过期缓存没被当成可用缓存继续用。
        self.assertEqual(len(client.calls), 4)
        kept = read_semester_cache(SELECTED_SEMESTER)
        self.assertEqual(kept["room_count"], ORCH_AUTUMN_RANGE[1])

    def test_expired_dictionary_is_not_usable_when_the_list_is_truncated(self):
        """字典只有过期缓存、刷新又拿到截断名单时停下：过期缓存不算可用。"""
        self.prime_year()
        self.prime_dictionary(orchestration_dictionary_names()[:1], self.expired_moment())
        client = self.fake_client(
            ("200", parent_page()),
            dictionary_body([{"jsid": "JSID-X", "jsmc": "格物楼101"}] * DICTIONARY_MAX_ROW),
        )
        result = self.run_query(client)
        self.assert_failure(result, "截断")
        # 字典这一步就停下，学期与本次课表都不再请求。
        self.assertEqual(len(client.calls), 2)

    def test_session_loss_stops_later_posts_and_keeps_validated_caches(self):
        """拉学期中途变成登录页时停止后续 POST，已经写盘的秋季缓存保持原样。"""
        rooms = orchestration_semester_rooms()
        names = orchestration_dictionary_names()
        client = self.fake_client(
            ("200", parent_page()),
            dictionary_body(self.dictionary_items(names)),
            self.semester_response(rooms[SELECTED_SEMESTER]),
            ("200", LOGIN_PAGE),
        )
        result = self.run_query(client)
        self.assert_failure(result, "登录页", "重新登录")
        # 秋季已经通过校验并写盘；春季拿到的登录页不写缓存，夏季与本次课表都不再请求。
        self.assertEqual(len(client.calls), 4)
        kept = read_semester_cache(SELECTED_SEMESTER)
        self.assertEqual(kept["room_count"], ORCH_AUTUMN_RANGE[1])
        self.assertIsNone(read_semester_cache("2026-2027-2"))
        self.assertIsNone(read_semester_cache("2026-2027-3"))

    def test_session_kick_on_the_query_page_fails_without_rooms(self):
        """本次课表拿到互踢提示时失败且没有 rooms，缓存不受影响。"""
        self.prime_year()
        self.prime_dictionary(orchestration_dictionary_names())
        client = self.fake_client(("200", parent_page()), ("200", SESSION_KICKED_PAGE))
        result = self.run_query(client)
        self.assert_failure(result, "互踢", "重新登录")
        self.assertEqual(len(client.calls), 2)
        kept = read_semester_cache("2026-2027-2")
        self.assertEqual(kept["room_count"], ORCH_SPRING_RANGE[1])

    def test_target_semester_outside_the_year_sends_no_post(self):
        """父页里没有本学年学期时只读了一次父页，一个 POST 都不发。"""
        client = self.fake_client(("200", parent_page(values=("2025-2026-1", "2025-2026-2"))))
        result = self.run_query(client)
        self.assert_failure(result, "无法使用该学期")
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0]["method"], "GET")

    def test_target_semester_missing_from_the_year_list_sends_no_post(self):
        """目标学期不在本学年下拉里时同样只读了一次父页。"""
        client = self.fake_client(("200", parent_page(values=("2026-2027-2", "2026-2027-3"))))
        result = self.run_query(client)
        self.assert_failure(result, "无法使用该学期")
        self.assertEqual(len(client.calls), 1)

    def test_invalid_parameters_send_no_request(self):
        """大节起点不在块首这类参数失败一个请求都不发。"""
        client = self.fake_client()
        result = self.run_query(client, period_start="4", period_end="4")
        self.assert_failure(result, "period_start=4")
        self.assertEqual(client.calls, [])

    def test_each_query_refreshes_its_resources_again(self):
        """同一会话连续查两次：两次都重读父页并重发本次课表，说明每次都清了刷新记录。"""
        self.prime_year()
        self.prime_dictionary(orchestration_dictionary_names())
        client = self.fake_client(
            ("200", parent_page()),
            self.query_response(),
            ("200", parent_page()),
            self.query_response(),
        )
        first = self.run_query(client)
        second = self.run_query(client)
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertEqual([call["method"] for call in client.calls], ["GET", "POST", "GET", "POST"])


class ClassroomFailurePagePropertyTest(ClassroomOrchestrationTestCase):
    """任务 11.2 / 设计 Correctness Properties 第 14 条：失败页不产生不上课教室。

    固定种子 20261018：把全部「失败页 × 它不可用的位置」组合打乱后跑 20 轮，每种组合至少跑一次
    （组合共 14 种，比轮数少）。断言结果都是失败、没有 rooms，失败页也没有被写进缓存，后面更不会
    再有请求。
    """

    SEED = 20261018
    ROUNDS = 20

    def test_failure_pages_never_produce_rooms(self):
        """登录页、非法访问、互踢、缺表、格数不是 35 落在可用位置上时都只得到失败结果。"""
        cases = self.shuffled_cases(random.Random(self.SEED))
        for round_index in range(self.ROUNDS):
            name, page, position, attempts = cases[round_index % len(cases)]
            with self.subTest(page=name, position=position, round_index=round_index):
                self.assert_failure_page(page, position, attempts)

    def shuffled_cases(self, generator):
        """列出全部「失败页 × 它不可用的位置」组合并按固定种子打乱，保证每种都跑到。"""
        cases = []
        for name, page, positions, attempts in FAILURE_PAGE_SAMPLES:
            for position in positions:
                cases.append((name, page, position, attempts))
        generator.shuffle(cases)
        return cases

    def assert_failure_page(self, page, position, attempts):
        """把失败页放到指定位置跑一次编排，断言失败、没有 rooms，也没写进缓存。"""
        client = self.failure_page_client(page, position, attempts)
        result = self.run_query(client)
        self.assertFalse(result["ok"])
        self.assertNotIn("rooms", result)
        self.assertTrue(result["error"])
        self.assertTrue(result["hint"])
        # 失败页不能变成这个学期的教室名缓存。
        if position == SEMESTER_POSITION:
            self.assertIsNone(read_semester_cache(SELECTED_SEMESTER))
        # 已经通过校验的学期缓存不会因为后面的失败页被删掉。
        if position != PARENT_POSITION:
            kept = read_semester_cache("2026-2027-2")
            self.assertEqual(kept["room_count"], ORCH_SPRING_RANGE[1])
        self.assertEqual(len(client.calls), self.expected_calls(position, attempts))

    def failure_page_client(self, page, position, attempts):
        """按插入位置拼响应队列与假客户端；每轮先清缓存，再把该位置之前的缓存准备好。"""
        self.clear_caches()
        pages = [("200", page)] * attempts
        # 父页位置：父页 GET 直接拿到失败页，后面的请求都不该发生。
        if position == PARENT_POSITION:
            return self.fake_client(*pages)
        self.prime_dictionary(orchestration_dictionary_names())
        rooms = orchestration_semester_rooms()
        # 学期位置：让秋季没有缓存，失败页就落在秋季那次 POST 上。
        if position == SEMESTER_POSITION:
            for semester in ("2026-2027-2", "2026-2027-3"):
                self.prime_semester(semester, rooms[semester])
            return self.fake_client(*([("200", parent_page())] + pages))
        # 本次查询位置：本学年三个学期都用未过期缓存，失败页落在本次课表 POST 上。
        for semester in EXPECTED_YEAR_SEMESTERS:
            self.prime_semester(semester, rooms[semester])
        return self.fake_client(*([("200", parent_page())] + pages))

    def clear_caches(self):
        """清掉缓存目录里的文件：同一用例跑多轮时每轮从同一个起点开始。"""
        # 目录还没建出来时没有要清的文件。
        if not os.path.isdir(self.cache_root):
            return
        for name in os.listdir(self.cache_root):
            os.remove(os.path.join(self.cache_root, name))

    def expected_calls(self, position, attempts):
        """算这次应该发几次请求：父页位置只有失败页本身，另外两处要先读一次父页。"""
        # 父页位置没有前置请求，另外两处都要先 GET 一次父页。
        if position == PARENT_POSITION:
            return attempts
        return 1 + attempts


if __name__ == "__main__":
    unittest.main()
if __name__ == "__main__":
    unittest.main()
