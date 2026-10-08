"""查无上课教室的纯规则：参数校验、本学年学期列表、父页与课表解析，以及缓存读写。

本模块不联网：请求与编排在后续任务里接上，缓存只落盘解析成功的正文结果。
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone

from .jwxt_auth import contains_any, strip_tags
from .jwxt_client import expand_path, state_dir, write_private_file
from .jwxt_html import LOGIN_MARKERS, attr, parse_table_html
from .jwxt_schedule import KBTABLE_RE
from .result import failure

# 学期格式：YYYY-YYYY-N，末位 1 秋、2 春、3 夏。
SEMESTER_RE = re.compile(r"^(\d{4})-(\d{4})-([123])$")

# 周次的合法闭区间，页面只提供 1 到 30 周。
WEEK_RANGE = (1, 30)

# 星期的合法闭区间，1 到 7 对应星期一至星期日。
WEEKDAY_RANGE = (1, 7)

# 节次的合法闭区间，一天最多 12 小节。
PERIOD_RANGE = (1, 12)

# 固定节次块：块名就是块内小节编号的拼接，顺序与表头一致。
PERIOD_BLOCKS = (
    ("0102", (1, 2)),
    ("030405", (3, 4, 5)),
    ("0607", (6, 7)),
    ("0809", (8, 9)),
    ("101112", (10, 11, 12)),
)

# 大节范围的允许起点：各块的第一小节，起点只能取这些值。
BLOCK_START_BOUNDS = tuple(periods[0] for _name, periods in PERIOD_BLOCKS)
# 大节取值不对时的提示：起点必须落在某块开头，终点写 1 到 12 里的哪一小节都按整块纳入。
BLOCK_BOUNDS_TEXT = (
    "大节范围取不出整块，起始取 "
    + "/".join(str(value) for value in BLOCK_START_BOUNDS)
    + "，结束取 1 到 12 的任意小节（其所在大节整块纳入）"
)

# 一周 7 天，每天 5 个节次块，课表网格因此固定 35 格。
WEEKDAYS = (1, 2, 3, 4, 5, 6, 7)

# 参数出错时的统一提示，指出这几个参数怎么传；周次是起止两值，省略 --week-end 时等于 --week。
PARAM_HINT = "使用 --semester、--week、--week-end、--weekday、--period-start、--period-end 传参数"

# 全角数字与半角数字的对应表，只用于名称规范化。
DIGIT_TRANSLATION = str.maketrans("０１２３４５６７８９", "0123456789")
# 房号：末尾的字母头加数字，可选字母尾缀；它前面的部分算楼栋前缀。
ROOM_NUMBER_RE = re.compile(r"[A-Za-z]*\d+[A-Za-z]?$")

# 房号开头的字母头，用来给「只有数字」的后项补字母。
ROOM_HEAD_RE = re.compile(r"[A-Za-z]*")

# 合称分隔符：顿号、半角句点和空白。
ROOM_SEPARATOR_RE = re.compile(r"[、.\s]+")

# 父页成功标题，标题不符说明拿到的不是教室课表页。
CLASSROOM_TITLE = "全校性教室课表"

# 父页表单里学期与节次模式两个字段名，解析时按字段名定位控件。
SEMESTER_FIELD = "xnxqh"
MODE_FIELD = "kbjcmsid"

# 登录页标题字样，与正文的 LOGIN_MARKERS 一起判定登录页。
LOGIN_TITLE_MARKERS = ("登录", "登陆")

# 正文里的会话互踢提示：与参数无关，改参数或重试都没用。
SESSION_KICKED_TEXT = "您的账号在其它地方登录"

# 正文里的非法访问提示：通常是接口路径误写成 kbxx。
ILLEGAL_ACCESS_TEXT = "非法访问"

# 正文里的节次出错提示：该学期没配课表节次，整份响应不可用。
PERIOD_ERROR_TEXT = "查询节次出错"

# 表头第 1 格文本，其后 35 格是节次块名。
HEADER_FIRST_CELL = "教室\\节次"

# 表头 35 个节次块名：5 个块按天重复，位置就是「星期 × 块」的落位口径。
GRID_BLOCK_NAMES = tuple(name for _weekday in WEEKDAYS for name, _periods in PERIOD_BLOCKS)

# 一行数据格的个数：7 天 × 5 块，表头行比它多一个「教室\节次」格。
GRID_CELL_COUNT = len(GRID_BLOCK_NAMES)

# 登录页或会话失效时的提示：只能重新登录。
LOGIN_HINT = "请运行 easy-qfnu jwxt login 重新登录后重试"
# 页面未被识别时的提示：路径误写成 kbxx 会返回非法访问。
PAGE_HINT = "确认接口路径是 kbcx 后重试"
# 父页缺少学期选中项或节次模式时的提示。
PARENT_HINT = "请从教务侧栏重新进入「全校性教室课表」后再试"
# 课表结构不符预期时的提示：不能按缺失的列推断空闲。
GRID_HINT = "课表结构与预期不符，请确认教务是否改版后重试"
# 字典不可用时的提示：旧字典能不能继续用由调用方按缓存期限判断。
DICTIONARY_HINT = "请稍后重试；本地有 7 日内旧字典时仍可继续使用旧字典"

# 缓存目录的环境变量：改的是目录，不是文件名。
CACHE_ENV_VAR = "QFNU_CLASSROOM_CACHE_PATH"
# 状态目录下的缓存子目录名，字典文件与各学期文件都放这里。
CACHE_DIR_NAME = "classroom-schedule"
# 字典缓存文件名。
DICTIONARY_CACHE_NAME = "dictionary.json"
# 学期缓存文件名后缀，学期值加它拼成 <学期>.json。
SEMESTER_CACHE_SUFFIX = ".json"
# 缓存有效期天数：超过 7 日才刷新，正好满 7 日仍算可用。
CACHE_TTL_DAYS = 7
# 缓存时间戳的时区：教务在东八区，写入时间固定带 +08:00。
CACHE_TIMEZONE = timezone(timedelta(hours=8))
# 完整学期的教室名数阈值：低于阈值说明响应被截断，不能当全年是否有课的证据。
SEMESTER_MIN_ROOMS = {
    "1": 200,  # 秋季
    "2": 200,  # 春季
    "3": 50,  # 夏季开课少，阈值低
}

# 页面标题，用来区分登录页、非法访问页与目标页面。
TITLE_RE = re.compile(r"(?is)<title\b[^>]*>(.*?)</title\s*>")
# 下拉的 option：第 1 组是完整开标签，第 2 组是显示文本。
OPTION_RE = re.compile(r"(?is)(<option\b[^>]*>)(.*?)</option\s*>")
# 数据格里的 div 开标签：class 带 kbcontent 就是课程块结构。
DIV_TAG_RE = re.compile(r"(?is)<div\b([^>]*)>")


def parse_semester(value):
    """把学期拆成排序键 (起始年, 末位)。

    格式不是 YYYY-YYYY-N 时返回 None，调用方按无效学期处理。
    """
    match = SEMESTER_RE.match(str(value if value is not None else "").strip())
    # 下拉里可能有「全部」这类非学期项，格式不符一律当无效。
    if match is None:
        return None
    return int(match.group(1)), int(match.group(3))


def parse_academic_year(value):
    """取学期所属的学年串，形如 `2026-2027`；格式不符时返回 None。

    学年由前两段判定，季节只看末位；两者分开取，避免把跨年或残缺的值当成同一学年。
    """
    match = SEMESTER_RE.match(str(value if value is not None else "").strip())
    # 下拉里可能有「全部」这类非学期项，格式不符时取不出学年。
    if match is None:
        return None
    return match.group(1) + "-" + match.group(2)


def year_semester_list(options, target):
    """取目标学年（本学年）的学期列表，返回 (列表, 警告)。

    列表含下拉里目标学年的秋季、春季与夏季，按学期末位从秋到夏排列：秋季和春季用于全年排课
    判断，夏季是否完整由反推阶段的完整阈值判定，这里只决定要拉取哪几个学期。
    格式不符的下拉项跳过并记警告；目标学年一项都没有时返回 None，调用方必须拒绝查询。
    """
    warnings = []
    target_year = parse_academic_year(target)
    # 取不出目标学年就没有可比对的学年，只能拒绝。
    if target_year is None:
        return None, warnings
    ranked = []
    for option in options or ():
        text = str(option if option is not None else "").strip()
        rank = parse_semester(text)
        # 格式不符的下拉项跳过并记警告，不参与学年比较。
        if rank is None:
            if text:
                warnings.append("学期下拉项格式不符，已跳过: " + text)
            continue
        # 只取目标学年的学期；其他学年（含未来学年）的项不进本学年列表。
        if parse_academic_year(text) != target_year:
            continue
        # 同一学年内按季节排：秋（1）、春（2）、夏（3）就是时间先后。
        ranked.append((rank[1], text))
    ranked.sort()
    semesters = [value for _season, value in ranked]
    # 本学年一个学期都没有时不发上游请求，直接拒绝查询。
    if not semesters:
        return None, warnings
    return semesters, warnings


def parse_bound(value, low, high):
    """把参数转成 [low, high] 内的整数；非整数或越界返回 None。"""
    text = str(value if value is not None else "").strip()
    # 空值、负号、小数点、字母都不是合法整数。
    if not text.isdigit():
        return None
    number = int(text)
    # 越界值同样算无效参数。
    if number < low or number > high:
        return None
    return number


def normalize_room_name(name):
    """规范化教室名：去首尾空白、压缩连续空白、全角数字转半角。

    不做 a 至 d 尾缀合并，JC1003 与 JC1003a 仍是两间教室。
    """
    text = str(name if name is not None else "").translate(DIGIT_TRANSLATION)
    # split() 会把全角空格和不换行空格也切开，再用半角空格拼回。
    return " ".join(text.split())

def split_room_range(chunk):
    """按 `-` 切开一个片段；只要有一侧取不出房号，就整段保留。

    不补中间房号：`数学楼401-403` 只得到 401 与 403，不会多出 402。
    """
    # 没有连字符的片段原样返回。
    if "-" not in chunk:
        return [chunk]
    pieces = chunk.split("-")
    # 两侧都能取出房号才切开，否则整段留给展开阶段当楼栋前缀处理。
    if any(ROOM_NUMBER_RE.search(piece) is None for piece in pieces):
        return [chunk]
    return pieces


def split_room_parts(name):
    """把合称展示名切成片段：先去首尾空白，再按顿号、句点和空白切开。"""
    parts = []
    for chunk in ROOM_SEPARATOR_RE.split(str(name if name is not None else "").strip()):
        # 连续分隔符会切出空片段，空片段不参与展开。
        if not chunk:
            continue
        parts.extend(split_room_range(chunk))
    return parts


def expand_room_name(name):
    """把合称展示名展开成单体教室，返回 name、rooms 与 expanded。

    房号是片段末尾的 `[A-Za-z]*\\d+[A-Za-z]?`，其余是楼栋前缀。后项只有数字和
    可选尾缀时补上前一项的房号字母头，后项已带字母头时只继承楼栋前缀。展开不出
    任何房号时 expanded 为 False、rooms 为空，原始展示名只能进警告，不得放进不上课结果。
    """
    display = str(name if name is not None else "").strip()
    rooms = []
    prefix = ""
    head = ""
    for part in split_room_parts(display):
        match = ROOM_NUMBER_RE.search(part)
        # 片段末尾没有房号时它是楼栋前缀，留给后面的片段继承。
        if match is None:
            prefix = part
            continue
        number = match.group(0)
        own_prefix = part[: match.start()]
        # 片段自带楼栋前缀时以它为准，否则沿用前一片段留下的楼栋前缀。
        if own_prefix:
            prefix = own_prefix
        own_head = ROOM_HEAD_RE.match(number).group(0)
        # 片段已带字母头时只继承楼栋前缀，不覆盖自己的字母。
        if own_head:
            head = own_head
        # 只有数字和可选尾缀时补上前一片段的房号字母头，例如 F101-102 得到 F102。
        else:
            number = head + number
        rooms.append(prefix + number)
    return {"name": display, "rooms": rooms, "expanded": bool(rooms)}


def expand_record(jsid, jsmc):
    """展开一条字典记录，返回 jsid、jsmc、rooms、source_jsid 与 expanded。

    合称记录的 jsid 只记在 source_jsid 上，不按展开出的教室拆成多个 ID。
    """
    expanded = expand_room_name(jsmc)
    return {
        "jsid": jsid,
        "jsmc": expanded["name"],
        "rooms": expanded["rooms"],
        "source_jsid": jsid,
        "expanded": expanded["expanded"],
    }


def validate_query(semester, week_start, week_end, weekday, period_start, period_end, keyword=""):
    """校验查询参数，返回 (参数, 失败结果)。

    任一参数无效时返回失败结果并指出参数名，调用方不得再发上游请求。结束周次或结束大节
    省略（None 或空串）时等于对应的起始值：周次只查一周，大节只查起始大节所在的那一块。
    """
    semester_text = str(semester if semester is not None else "").strip()
    # 学期格式不对就构造不出课表 POST 的 xnxqh。
    if parse_semester(semester_text) is None:
        return None, failure("jwxt", "学期格式必须是 YYYY-YYYY-N: semester=" + semester_text, PARAM_HINT)
    # 起始周次越界就不能交给服务端过滤，直接拒绝。
    start_week = parse_bound(week_start, *WEEK_RANGE)
    if start_week is None:
        return None, failure("jwxt", "起始周次必须在 1 到 30 之间: week_start=" + str(week_start), PARAM_HINT)
    # 省略结束周次时与起始周次相同，相当于只查这一周。
    if week_end is None or str(week_end).strip() == "":
        end_week = start_week
    else:
        end_week = parse_bound(week_end, *WEEK_RANGE)
        # 结束周次越界时同样拼不出可交给服务端过滤的闭区间。
        if end_week is None:
            return None, failure("jwxt", "结束周次必须在 1 到 30 之间: week_end=" + str(week_end), PARAM_HINT)
    # 起始周次大于结束周次时闭区间为空，一周都查不出来，直接拒绝。
    if start_week > end_week:
        message = "起始周次不能大于结束周次: week_start=" + str(start_week) + ", week_end=" + str(end_week)
        return None, failure("jwxt", message, PARAM_HINT)
    # 星期只查一天：多天写法会把不同天的格混在一次响应里，越界值也定位不到列。
    weekday_value = parse_bound(weekday, *WEEKDAY_RANGE)
    if weekday_value is None:
        message = "星期只接受 1 到 7 的单个值，本功能只查单天: weekday=" + str(weekday)
        return None, failure("jwxt", message, PARAM_HINT)
    start_period = parse_bound(period_start, *PERIOD_RANGE)
    # 省略结束大节时与起始大节相同，表示只查起始大节所在的那一块。
    if period_end is None or str(period_end).strip() == "":
        end_period = start_period
    else:
        end_period = parse_bound(period_end, *PERIOD_RANGE)
    bound_error = block_bounds_error(start_period, end_period)
    # 大节范围取不出整块时读不出用户想要的区间，拒绝且不发上游请求。
    if bound_error is not None:
        message = bound_error + ": period_start=" + str(period_start) + ", period_end=" + str(period_end)
        return None, failure("jwxt", message, PARAM_HINT)
    params = {
        "semester": semester_text,
        "week_start": start_week,
        "week_end": end_week,
        "weekday": weekday_value,
        "period_start": start_period,
        "period_end": end_period,
        "keyword": normalize_room_name(keyword),
    }
    return params, None


def block_periods(block_name):
    """按块名取小节编号；块名不在约定表里返回空元组。"""
    for name, periods in PERIOD_BLOCKS:
        # 只有表头块名与约定一致时才能确定它覆盖哪些小节。
        if name == block_name:
            return periods
    return ()


def block_bounds_error(period_start, period_end):
    """大节范围取不出整块时返回错误描述，合法时返回 None。

    入参是已经过 parse_bound 的整数或 None。起点必须是某块的第一小节；终点取 1 到 12 的
    任意小节，它所在的大节整块纳入，所以 3–4 与 3–5 的覆盖块都只有 030405。起点落在块
    中间时第一个块只有一半进范围，服务端按整块给格，读不出用户想要的区间，因此拒绝。
    """
    # 起点缺失或不落在某块的第一小节上时，第一个块只有一半进范围，不猜用户想查哪一块。
    if period_start not in BLOCK_START_BOUNDS:
        return BLOCK_BOUNDS_TEXT
    # 终点缺失或超出可查的小节范围时拼不出闭区间，同样算参数失败。
    if period_end is None or not PERIOD_RANGE[0] <= period_end <= PERIOD_RANGE[1]:
        return BLOCK_BOUNDS_TEXT
    # 起点晚于终点时闭区间为空，整块也没有交集，直接拒绝。
    if period_start > period_end:
        return "起始大节不能大于结束大节"
    return None


def query_blocks(period_start, period_end):
    """取与闭区间 [period_start, period_end] 相交的节次块名，按表头顺序返回。

    范围按整块取：起点不在块首、终点超出 1 到 12 或起止倒置时没有可读的覆盖块，返回空
    列表，调用方按参数失败处理，不发上游请求。
    """
    start = parse_bound(period_start, *PERIOD_RANGE)
    end = parse_bound(period_end, *PERIOD_RANGE)
    # 起点不在块首、终点越界或起止倒置时都拼不出整块，没有可读的覆盖块。
    if start is None or end is None or block_bounds_error(start, end) is not None:
        return []
    blocks = []
    for name, periods in PERIOD_BLOCKS:
        # 块内任一小节落在闭区间内，整块都进查询范围。
        if any(start <= period <= end for period in periods):
            blocks.append(name)
    return blocks

def page_title(raw):
    """取页面标题文本；没有 title 标签时返回空串，调用方按标题不符处理。"""
    match = TITLE_RE.search(raw)
    # 没有标题就无从判断这是不是目标页面。
    if match is None:
        return ""
    return strip_tags(match.group(1))


def is_login_page(raw):
    """标题含登录字样，或正文出现登录表单标记时判为登录页。"""
    # 标题写着登录页时不必再看正文。
    if contains_any(page_title(raw), LOGIN_TITLE_MARKERS):
        return True
    # 正文出现账号、密码、验证码标记说明返回的是登录表单。
    return contains_any(raw, LOGIN_MARKERS)


def page_failure(raw, expected_title=""):
    """页面不可用时返回失败结果，可用时返回 None。

    登录页、会话互踢提示与「非法访问」都与参数无关，先判这三类；expected_title 非空时再
    比对标题。失败结果不含 rooms，调用方不得写缓存，也不得产生不上课教室。
    """
    # 登录页说明会话已失效，改参数没用，只能重新登录。
    if is_login_page(raw):
        return failure("jwxt", "响应是登录页，本次查询已停止", LOGIN_HINT)
    # 互踢提示说明会话在别处登录或已过期，同样与参数无关。
    if SESSION_KICKED_TEXT in raw:
        message = "响应是会话互踢提示，与参数无关: " + SESSION_KICKED_TEXT
        return failure("jwxt", message, LOGIN_HINT)
    # 非法访问通常是接口路径误写，页面内容不是课表。
    if ILLEGAL_ACCESS_TEXT in raw:
        return failure("jwxt", "响应是非法访问提示，页面未被识别", PAGE_HINT)
    # 目标页面有固定标题时，标题不符说明拿到的不是该页面。
    if expected_title:
        title = page_title(raw)
        # 标题为空也算不符：只能确认它不是目标页面。
        if title != expected_title:
            message = "页面标题不是「" + expected_title + "」: title=" + (title or "(空)")
            return failure("jwxt", message, PAGE_HINT)
    return None


def open_tag(raw, tag, field):
    """定位 name 或 id 等于 field 的开标签，返回匹配对象；找不到返回 None。"""
    pattern = re.compile(
        r"(?is)<"
        + re.escape(tag)
        + r"\b(?=[^>]*\b(?:name|id)\s*=\s*[\"']?"
        + re.escape(field)
        + r"[\"'\s>])[^>]*>"
    )
    return pattern.search(raw)


def select_options(raw, field):
    """取下拉控件的 (全部 option value, 当前选中 value)。

    下拉缺失、没有闭合标签或一个 option 都没有时选中值返回空串，调用方按字段缺失处理。
    一个 option 都没写 selected 时按浏览器口径取第一项：真实父页的「时间模式」下拉只有
    一个 option 且不带 selected，浏览器默认就选中它。
    """
    tag = open_tag(raw, "select", field)
    # 没有这个下拉控件就取不出任何取值。
    if tag is None:
        return [], ""
    end = raw.find("</select", tag.end())
    # 下拉没有闭合标签时范围不确定，当作缺失。
    if end < 0:
        return [], ""
    values = []
    selected = ""
    for option_tag, inner in OPTION_RE.findall(raw[tag.end():end]):
        value = attr(option_tag, "value").strip()
        # option 没写 value 时浏览器提交的是显示文本，按同一口径取值。
        if not value:
            value = strip_tags(inner)
        values.append(value)
        # 带 selected 属性的项就是当前选中项。
        if "selected" in option_tag.lower():
            selected = value
    # 一个 selected 都没写时浏览器默认选中第一项，这里按同一口径取值。
    if not selected and values:
        selected = values[0]
    return values, selected


def field_value(raw, field):
    """取字段的当前值：优先下拉选中项，其次同名输入框的 value；都没有返回空串。"""
    _values, selected = select_options(raw, field)
    # 下拉有选中项时它就是当前值，不必再看输入框。
    if selected:
        return selected
    tag = open_tag(raw, "input", field)
    # 没有这个输入框（或没写 value）时返回空串，调用方按字段缺失处理。
    if tag is None:
        return ""
    return attr(tag.group(0), "value").strip()


def parse_classroom_page(raw):
    """解析教室课表父页，返回 (页面信息, 失败结果)。

    页面信息含 semesters（学期下拉全部 value）、selected（当前选中学期）与 kbjcmsid。
    登录页、非法访问、会话互踢、标题不是「全校性教室课表」，以及没有选中学期或没有节次
    模式时返回失败结果：调用方不得继续发请求，也不得写缓存。
    """
    error = page_failure(raw, CLASSROOM_TITLE)
    # 页面本身不可用时，后面的字段读到什么都不作数。
    if error is not None:
        return None, error
    semesters, selected = select_options(raw, SEMESTER_FIELD)
    # 没有选中学期就定不了目标学期，也推不出本学年。
    if not selected:
        return None, failure("jwxt", "父页没有选中的学期", PARENT_HINT)
    mode = field_value(raw, MODE_FIELD)
    # 没有节次模式就拼不出课表 POST 的 kbjcmsid，该学期的节次不可用。
    if not mode:
        return None, failure("jwxt", "父页没有 kbjcmsid，该学期节次模式不可用", PARENT_HINT)
    return {"semesters": semesters, "selected": selected, "kbjcmsid": mode}, None

def parse_dictionary(text, max_row):
    """解析 queryJs2 的 JSON，返回 (字典, 失败结果)。

    字典含 max_row、records 与 warnings，每条记录只收 jsid 与 jsmc。result 不是 true，
    或 list 长度等于请求的 max_row（说明名单被上限截断）时拒绝该次名单：残缺名单不能当成
    教室全集。已发布的教室总表只作规模参照，不作运行时输入。
    """
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return None, failure("jwxt", "教室字典响应不是合法 JSON", DICTIONARY_HINT)
    # 响应不是对象时取不出 result 与 list。
    if not isinstance(data, dict):
        return None, failure("jwxt", "教室字典响应的结构不是对象", DICTIONARY_HINT)
    # result 不是 true 说明这次请求没拿到名单，不能用它当全集。
    if data.get("result") is not True:
        return None, failure("jwxt", "教室字典返回的 result 不是 true", DICTIONARY_HINT)
    raw_list = data.get("list")
    # list 不是数组时同样取不出记录。
    if not isinstance(raw_list, list):
        return None, failure("jwxt", "教室字典返回的 list 不是数组", DICTIONARY_HINT)
    # 长度等于请求上限说明名单被截断，残缺名单不能当成全集。
    if len(raw_list) == max_row:
        message = "教室字典被截断: list 长度 " + str(len(raw_list)) + " 等于 maxRow"
        return None, failure("jwxt", message, DICTIONARY_HINT)
    records = []
    warnings = []
    for item in raw_list:
        record = dictionary_record(item, warnings)
        # 取不出 jsid 或 jsmc 的记录不进字典，警告已在解析那条记录时记下。
        if record is None:
            continue
        records.append(record)
    dictionary = {"max_row": max_row, "records": records, "warnings": warnings}
    return dictionary, None


def dictionary_record(item, warnings):
    """把一条字典记录解成 jsid、jsmc 与展开结果；取不出 jsid 或 jsmc 时返回 None。

    只收 jsid 与 jsmc，其他字段一律不进字典。展开不出房号的展示名仍留在字典里并记警告：
    它不能进不上课结果。
    """
    # 不是对象的条目取不出这两个字段。
    if not isinstance(item, dict):
        warnings.append("字典记录不是对象，已跳过")
        return None
    jsid = str(item.get("jsid") or "").strip()
    jsmc = str(item.get("jsmc") or "").strip()
    # 缺 jsid 的记录对不上教室身份，缺 jsmc 的没有展示名，都只能跳过。
    if not jsid or not jsmc:
        warnings.append("字典记录缺少 jsid 或 jsmc，已跳过: " + (jsid or jsmc or "(空)"))
        return None
    record = expand_record(jsid, jsmc)
    # 展开失败的展示名不能进不上课结果，原文进警告。
    if not record["expanded"]:
        warnings.append("教室名无法展开，不进不上课结果: " + jsmc)
    return record


def cell_text(cell):
    """取单元格的展示文本：去标签并压缩空白。"""
    return strip_tags(cell)


def header_row_index(rows):
    """找表头行下标：首格是「教室\\节次」且其后 35 格是节次块名的那一行。

    表头上面还有一行 7 天的星期标题，所以不能假定第 1 行就是表头。找不到返回 None。
    """
    for index, row in enumerate(rows):
        # 格数不符的行不可能是表头，先按格数筛掉。
        if len(row) != GRID_CELL_COUNT + 1:
            continue
        # 第 1 格文本不是表头首格时，这一行不是节次块表头。
        if cell_text(row[0]) != HEADER_FIRST_CELL:
            continue
        # 其后 35 格必须是按天重复的 5 个节次块名，否则星期与块的落位无从确定。
        if tuple(cell_text(cell) for cell in row[1:]) != GRID_BLOCK_NAMES:
            continue
        return index
    return None


def has_class_block(cell_html):
    """判断一格是否含课程块结构：有 class 带 kbcontent 的 div 即为有课。

    只看结构不读文字：空格的原文是 NOBR 包着的 &nbsp;，而真实课程正文里也会出现
    &nbsp;，按文本非空判会把空格当有课。
    """
    for match in DIV_TAG_RE.finditer(cell_html):
        # 只有 class 属性里带 kbcontent 的 div 才是课程块，其他 div 不参与判定。
        if "kbcontent" in attr(match.group(0), "class").lower():
            return True
    return False


def row_occupancy(cells):
    """把一行 35 个数据格压成占用位，返回 {星期: {块名: 布尔值}}。

    格数不是 35 时返回 None，调用方按课表结构变化处理。判据是格内有没有课程块结构，
    单元格文字一律不读。
    """
    # 格数不是 7 天 × 5 块时列与块的对应关系断了，不能按缺失的列推断空闲。
    if len(cells) != GRID_CELL_COUNT:
        return None
    occupancy = {}
    index = 0
    for weekday in WEEKDAYS:
        occupancy[weekday] = {}
        for name, _periods in PERIOD_BLOCKS:
            # 有课程块结构就是有课，块内小节共用这一格。
            occupancy[weekday][name] = has_class_block(cells[index])
            index += 1
    return occupancy


def row_name_warning(name):
    """首格取不出教室名时的警告文本：空首格与无法展开要分开说明。"""
    # 首格完全为空时这一行没有教室名。
    if not name:
        return "课表行首格取不出教室名，已跳过该行"
    return "教室名无法展开，不进不上课结果: " + name


def classroom_table_rows(raw):
    """取课表表格的数据行，返回 (数据行, 失败结果)。

    先判登录页、会话互踢与非法访问，再判正文「查询节次出错」，然后定位闭合的
    `table#kbtable` 并校验表头；表头之后的每一行都是数据行。
    """
    error = page_failure(raw)
    # 页面不可用时不必再找表格。
    if error is not None:
        return None, error
    # 正文写「查询节次出错」说明该学期没配课表节次，与网格结构无关，单独说明。
    if PERIOD_ERROR_TEXT in raw:
        return None, failure("jwxt", "该学期查询节次出错，节次模式不可用", PARENT_HINT)
    match = KBTABLE_RE.search(raw)
    # 没有闭合的 table#kbtable 就拿不到 35 格，响应不可用。
    if match is None:
        return None, failure("jwxt", "响应里没有闭合的 table#kbtable", GRID_HINT)
    rows = parse_table_html(match.group(0))
    index = header_row_index(rows)
    # 表头不符预期说明课表结构变了，星期与块的落位无从确定。
    if index is None:
        message = "课表表头不是「" + HEADER_FIRST_CELL + "」或节次块不符预期"
        return None, failure("jwxt", message, GRID_HINT)
    return rows[index + 1:], None


def parse_classroom_table(raw):
    """解析教室课表响应，返回 (解析结果, 失败结果)。

    解析结果含 rows（每行的展示名、展开出的单体教室与占用位）与 warnings。登录页、会话
    互踢、非法访问、正文「查询节次出错」、缺闭合 `table#kbtable`、表头不符、任一数据行
    格数不是 35、没有一行首格能取出教室名时整份响应不可用：调用方不得写缓存，也不得产生
    不上课教室。
    """
    rows, error = classroom_table_rows(raw)
    # 页面或表格不可用时整份响应都不作数。
    if error is not None:
        return None, error
    parsed = []
    warnings = []
    for row in rows:
        # 任一数据行格数不是 35 说明课表结构变了，不能用缺失的列推断空闲。
        if len(row) != GRID_CELL_COUNT + 1:
            message = "数据行格数不是 " + str(GRID_CELL_COUNT) + ": cells=" + str(len(row))
            return None, failure("jwxt", message, GRID_HINT)
        name = cell_text(row[0])
        expanded = expand_room_name(name)
        # 首格取不出教室名的行只进警告：既不进占用集，也不产生结果行。
        if not expanded["expanded"]:
            warnings.append(row_name_warning(name))
            continue
        parsed.append(
            {
                "name": expanded["name"],
                "rooms": expanded["rooms"],
                "occupancy": row_occupancy(row[1:]),
            }
        )
    # 一行都取不出教室名时整份响应不可用，否则会把「全部教室都不上课」当成结论。
    if not parsed:
        return None, failure("jwxt", "课表没有一行首格能取出教室名", GRID_HINT)
    return {"rows": parsed, "warnings": warnings}, None


def room_occupancy_map(parsed):
    """把逐行占用位摊平成 单体教室 → 占用位；同名教室取先出现的那一行。

    合称行的占用位就是该行那一份，展开出的每间单体教室共用它，不拆成多份。
    """
    rooms = {}
    for row in parsed["rows"]:
        for name in row["rooms"]:
            # 同名教室第二次出现时保留先出现的那一份占用位。
            if name in rooms:
                continue
            rooms[name] = row["occupancy"]
    return rooms


def any_occupied(occupancy, weekday, blocks):
    """判断某天的给定块里是否至少有一个占用格；占用位缺失按没有课处理。

    块内小节共用一格，所以给定块里任一格有课就说明该大节有课。
    """
    day = occupancy.get(weekday, {})
    for name in blocks:
        # 给定块里有一块被占用就算占用。
        if day.get(name, False):
            return True
    return False


def free_blocks_of_day(occupancy, weekday):
    """取某天没有课的块名，按表头顺序返回。

    占用位为空（例如响应里没有这间教室的行）时 5 个块都空闲。空闲只表示没有排课，不表示
    可以占用。
    """
    day = occupancy.get(weekday, {})
    free = []
    for name, _periods in PERIOD_BLOCKS:
        # 该块在这一天没有占用格时算空闲。
        if not day.get(name, False):
            free.append(name)
    return free


# 缓存层：只把解析成功的字典与学期教室名写盘，读不出内容时按没有缓存处理。


def cache_dir():
    """取缓存目录；环境变量改的是目录，不是文件名。

    QFNU_CLASSROOM_CACHE_PATH 指定整目录，字典文件与各学期文件都落在该目录；没给环境变量时
    用状态目录下的 classroom-schedule 子目录。
    """
    value = os.environ.get(CACHE_ENV_VAR, "").strip()
    # 指定了环境变量就整目录用它，文件名仍由本模块决定。
    if value:
        return expand_path(value)
    return os.path.join(state_dir(), CACHE_DIR_NAME)


def dictionary_cache_path():
    """取字典缓存文件路径：缓存目录下的 dictionary.json。"""
    return os.path.join(cache_dir(), DICTIONARY_CACHE_NAME)


def semester_cache_path(semester):
    """取某学期的缓存文件路径；学期格式不是 YYYY-YYYY-N 时返回空串。

    学期值直接参与拼文件名，格式不符的值（例如带路径分隔符）不得用来拼路径，因此按没有
    这个学期的缓存处理。
    """
    text = str(semester if semester is not None else "").strip()
    # 格式不符时拼不出安全的文件名，调用方只能当这个学期没有缓存。
    if parse_semester(text) is None:
        return ""
    return os.path.join(cache_dir(), text + SEMESTER_CACHE_SUFFIX)


def now_moment():
    """取当前时刻，固定东八区；缓存时间戳都从它出发，避免各处各算一遍时区。"""
    return datetime.now(CACHE_TIMEZONE)


def timestamp_text(moment):
    """把时刻写成缓存时间戳：东八区、秒精度，形如 2026-10-08T09:30:00+08:00。"""
    # 不带时区的时刻按东八区理解，写出的时间戳不随运行机器的本地时区变化。
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=CACHE_TIMEZONE)
    return moment.astimezone(CACHE_TIMEZONE).isoformat(timespec="seconds")


def parse_timestamp(value):
    """把时间戳读成带时区的时刻；读不出或没有时区时返回 None。

    没有时区的值不猜它是哪个时区，按读不出处理，调用方把该缓存当成过期去刷新。
    """
    moment = value if isinstance(value, datetime) else None
    # 文本时间戳按 ISO 解析，末尾的 Z 当作 UTC。
    if moment is None:
        text = str(value if value is not None else "").strip()
        # 空值读不出时刻。
        if not text:
            return None
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    # 没有时区的时刻无法与另一个时刻相减，按读不出处理。
    if moment.tzinfo is None:
        return None
    return moment


def cache_expired(fetched_at, now):
    """判断缓存是否过期：写入到现在的间隔超过 7 日才算过期。

    纯函数：写入时间与当前时间都由调用方传入，函数内部不读系统时钟，编排与测试都好复用。
    正好满 7 日仍算可用（需求口径是早于 7 日才刷新）；时间戳读不出时按过期处理，调用方会去
    刷新这份缓存。字典与学期缓存共用它。
    """
    written = parse_timestamp(fetched_at)
    moment = parse_timestamp(now)
    # 任一时间戳读不出时判断不了新鲜度，只能按过期处理。
    if written is None or moment is None:
        return True
    return moment - written > timedelta(days=CACHE_TTL_DAYS)


def semester_complete(semester, room_count):
    """判断某学期的教室名数是否达到完整阈值；学期格式不符时返回 False。

    秋季、春季要至少 200 间，夏季至少 50 间：低于阈值说明响应不完整，该学期既不能证明有
    排课，也不能证明全年无课。
    """
    rank = parse_semester(semester)
    # 学期格式不符时取不出季节，无从判断阈值。
    if rank is None:
        return False
    return room_count >= SEMESTER_MIN_ROOMS[str(rank[1])]


def parsed_room_names(parsed):
    """取课表解析结果里各数据行首格展开出的单体教室名，去重后排序。

    学期缓存只存教室名：判定用不到占用位与单元格内容，所以只取首格结果。
    """
    names = set()
    for row in parsed["rows"]:
        for name in row["rooms"]:
            names.add(name)
    return sorted(names)


def write_cache_file(path, payload):
    """把缓存对象写成 JSON 文件：目录随文件建出，文件按私有权限写。"""
    body = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    write_private_file(path, body, "w")


def read_cache_file(path):
    """读缓存文件并返回里面的对象；路径为空、文件缺失、读不动或不是对象时返回 None。

    缓存缺失或损坏都按「没有缓存」处理：不做部分解析，调用方会去刷新。
    """
    # 拼不出路径（学期格式不符）时没有对应的缓存文件。
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    # 缓存内容不是对象时读不出字段，按没有缓存处理。
    if not isinstance(data, dict):
        return None
    return data


def write_dictionary_cache(dictionary, fetched_at=None):
    """把教室字典写入缓存文件，返回写入的内容。

    只写设计约定的字段：fetched_at、max_row、record_count、room_count 与每条记录的 jsid、
    jsmc、rooms。字典不是一次成功解析的结果（解析失败拿到的 None 等）时直接报错不写：残缺
    名单不能当全集。文件里不保存 Cookie、encoded、账号密码，也不保存任何单元格内容。
    """
    # 解析失败拿到的 None 或其他类型不是字典解析结果，属于调用方式错误。
    if not isinstance(dictionary, dict):
        raise TypeError("dictionary cache requires a parsed dictionary")
    records = dictionary.get("records")
    # 缺记录列表说明它不是成功解析的结果，残缺名单不允许写盘。
    if not isinstance(records, list):
        raise TypeError("dictionary cache requires parsed records")
    # 一条记录都没有的字典不是全量名单，同样不允许写盘。
    if not records:
        raise ValueError("dictionary cache requires at least one record")
    cached_records = []
    room_count = 0
    for record in records:
        rooms = list(record.get("rooms") or ())
        room_count += len(rooms)
        cached_records.append(
            {
                "jsid": str(record.get("jsid") or ""),
                "jsmc": str(record.get("jsmc") or ""),
                "rooms": rooms,
            }
        )
    payload = {
        "fetched_at": timestamp_text(fetched_at if fetched_at is not None else now_moment()),
        "max_row": dictionary.get("max_row"),
        "record_count": len(cached_records),
        "room_count": room_count,
        "records": cached_records,
    }
    write_cache_file(dictionary_cache_path(), payload)
    return payload


def read_dictionary_cache():
    """读字典缓存，返回缓存内容；缺失、损坏或字段不全时返回 None。"""
    payload = read_cache_file(dictionary_cache_path())
    # 没有缓存文件时就是没有可用字典。
    if payload is None:
        return None
    # records 不是数组说明缓存字段不全，不能拿它当教室全集。
    if not isinstance(payload.get("records"), list):
        return None
    return payload


def write_semester_cache(semester, kbjcmsid, parsed, fetched_at=None):
    """把某学期有排课的教室名写入缓存文件，返回写入的内容。

    只收通过完整性校验的课表解析结果：解析失败拿到的 None 与没有数据行的结果都直接报错不写，
    残缺 HTML、登录页、会话互踢、格数不是 35 的页面都进不到这里。缓存只存教室名，不存占用位、
    单元格内容、Cookie 或 encoded。
    """
    # 解析失败拿到的 None 或其他类型不是课表解析结果，属于调用方式错误。
    if not isinstance(parsed, dict):
        raise TypeError("semester cache requires a parsed classroom table")
    rows = parsed.get("rows")
    # 没有数据行说明解析结果残缺，不写缓存，避免把残缺名单当成该学期的全部教室。
    if not rows:
        raise ValueError("semester cache requires parsed rows")
    path = semester_cache_path(semester)
    # 学期格式不符时拼不出缓存文件名，这个学期的教室名没有可落盘的位置。
    if not path:
        raise ValueError("semester cache requires a semester like 2026-2027-1")
    rooms = parsed_room_names(parsed)
    payload = {
        "semester": str(semester).strip(),
        "kbjcmsid": str(kbjcmsid if kbjcmsid is not None else "").strip(),
        "fetched_at": timestamp_text(fetched_at if fetched_at is not None else now_moment()),
        "complete": semester_complete(semester, len(rooms)),
        "source_row_count": len(rows),
        "room_count": len(rooms),
        "rooms": rooms,
    }
    write_cache_file(path, payload)
    return payload


def read_semester_cache(semester):
    """读某学期的教室名缓存，返回缓存内容；缺失、损坏或字段不全时返回 None。"""
    payload = read_cache_file(semester_cache_path(semester))
    # 没有缓存文件时就是这个学期没有教室名缓存。
    if payload is None:
        return None
    # rooms 不是数组说明缓存字段不全，不能拿它当教室名列表。
    if not isinstance(payload.get("rooms"), list):
        return None
    # 文件里的学期与要读的学期不一致说明文件被换过，按没有缓存处理。
    if str(payload.get("semester") or "") != str(semester or "").strip():
        return None
    return payload


def write_semester_cache_from_page(semester, kbjcmsid, raw, fetched_at=None):
    """解析一份学期课表正文并写缓存，返回 (缓存内容, 失败结果)。

    正文不可用（登录页、会话互踢、非法访问、缺 kbtable、格数不是 35、查询节次出错）时只返回
    失败结果且不落盘：这类页面必须整份弃用，不能把残缺名单写进缓存。
    """
    parsed, error = parse_classroom_table(raw)
    # 响应不可用时缓存文件保持原样。
    if error is not None:
        return None, error
    return write_semester_cache(semester, kbjcmsid, parsed, fetched_at), None
