"""查无上课教室的纯规则与只读请求：参数校验、学期列表、页面解析、缓存读写与请求构造。

本模块只发学生端教室课表的三条只读请求，按固定顺序刷新缓存并只发一次带时间参数的课表，把
残缺正文挡在缓存之外；反推与结果组装在反推层。
"""

import http.client
import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from .jwxt_auth import contains_any, strip_tags
from .jwxt_client import JWXT_BASE, MAIN_URL, expand_path, state_dir, write_private_file
from .jwxt_html import LOGIN_MARKERS, attr, parse_table_html
from .jwxt_schedule import KBTABLE_RE
from .result import failure, success

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
# 页面未被识别时的提示：路径误写成 kbxx，或接口入参与以前不一样了。
PAGE_HINT = "接口路径或入参可能已变化，请确认路径仍是 kbcx 后重试"
# 父页缺少学期选中项或节次模式时的提示。
PARENT_HINT = "请从教务侧栏重新进入「全校性教室课表」后再试"
# 课表结构不符预期时的提示：不能按缺失的列推断空闲，这类页面同样不重试。
GRID_HINT = "接口路径或入参可能已变化，请确认教务课表是否改版后重试"

# 缓存目录的环境变量：改的是目录，不是文件名。
CACHE_ENV_VAR = "QFNU_CLASSROOM_CACHE_PATH"
# 状态目录下的缓存子目录名，各学期的教室名文件都放这里。
CACHE_DIR_NAME = "classroom-schedule"
# 学期缓存文件名后缀，学期值加它拼成 <学期>.json。
SEMESTER_CACHE_SUFFIX = ".json"
# 本地累计里内容不合法的学期文件的警告前缀，后面接学期与具体原因。
LOCAL_CACHE_BAD_WARNING = "本地累计学期缓存不可用，已跳过: "
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

# 内置教室总表：随仓库分发的一次抓取快照，运行时只读它，不再请求教室字典接口。
ROSTER_FILE_NAME = "classroom-roster.json"
# 总表与本模块同目录，随包一起分发。
ROSTER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ROSTER_FILE_NAME)
# 总表不可用时的警告前缀，后面接具体原因；此时 jsid 与全年无课计数不可用。
ROSTER_UNAVAILABLE_WARNING = "内置教室总表不可用，jsid 与全年无课计数不可用: "

# 页面标题，用来区分登录页、非法访问页与目标页面。
TITLE_RE = re.compile(r"(?is)<title\b[^>]*>(.*?)</title\s*>")
# 下拉的 option：第 1 组是完整开标签，第 2 组是显示文本。
OPTION_RE = re.compile(r"(?is)(<option\b[^>]*>)(.*?)</option\s*>")
# 数据格里的 div 开标签：class 带 kbcontent 就是课程块结构。
DIV_TAG_RE = re.compile(r"(?is)<div\b([^>]*)>")

# 请求层常量：两个学生端教室接口的地址、请求头与重试次数。

# 父页：读学期下拉与当前节次模式，路径段必须是 kbcx。
CLASSROOM_PAGE_URL = JWXT_BASE + "/jsxsd/kbcx/kbxx_classroom"
# 教室课表：学期教室名与本次查询都用它；路径误写成 kbxx 会返回非法访问。
CLASSROOM_IFR_URL = JWXT_BASE + "/jsxsd/kbcx/kbxx_classroom_ifr"
# 两个 POST 的 Referer 是父页，与浏览器从父页发起请求时的写法一致。
CLASSROOM_REFERER = CLASSROOM_PAGE_URL
# 教室请求专用的桌面版 Chrome 标识：只作用于这两条请求，不动 jwxt_client 的全局 UA。
CLASSROOM_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
# 两个 POST 的表单编码类型，与既有 jwxt 请求保持一致。
FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"
# 请求次数上限：首次之外最多再重发 2 次，用于传输被掐断与正文不完整。
FETCH_ATTEMPTS = 3
# 传输中断的提示：这类失败与参数无关，重发由本模块负责。
TRANSPORT_HINT = "响应传输中断，请稍后重试"
# 正文连续不完整时的口径：说明响应没传完，与接口路径或入参无关。
INCOMPLETE_TEXT = "响应不完整"
# 正文不完整时的提示：不指向接口契约，只提示稍后重试。
INCOMPLETE_HINT = "请稍后重试；课表多次不完整时请确认教务是否改版"
# 串行刷新的资源名：父页一份，学期按学期值分开。
CLASSROOM_PAGE_RESOURCE = "classroom-page"
SEMESTER_RESOURCE_PREFIX = "semester:"

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
    """展开一条总表记录，返回 jsid、jsmc、rooms、source_jsid 与 expanded。

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

# 内置总表层：读随仓库分发的快照并展开成与既有字典解析一致的形状，全部不抛异常。


def read_roster_json(path):
    """读总表数据文件并解成 JSON 对象，返回 (对象, 失败说明)。

    文件缺失、读不动或内容不是合法 JSON 时返回 (None, 说明)：总表是本地数据文件，读不出来
    只影响 jsid 与全年无课计数，不该让整个查询失败。
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle), ""
    except (OSError, ValueError) as exc:
        return None, "文件读不出或不是合法 JSON: " + str(exc)


def empty_roster(reason):
    """取空的全集字典：总表不可用时下游仍拿到同一形状，原因记成一条警告。"""
    return {
        "max_row": 0,
        "captured_at": "",
        "records": [],
        "warnings": [ROSTER_UNAVAILABLE_WARNING + reason],
    }


def roster_items(payload):
    """取总表里的房间列表，返回 (列表, 失败说明)。

    顶层不是对象、rooms 不是数组或某条不是 [jsid, jsmc] 两元数组时返回 (None, 说明)：形状
    不对说明整份快照都不可信，不能跳过坏条目、把剩下的残缺名单当教室全集。
    """
    # 顶层不是对象时取不出 rooms 字段。
    if not isinstance(payload, dict):
        return None, "顶层不是对象"
    items = payload.get("rooms")
    # rooms 不是数组时取不出任何教室记录。
    if not isinstance(items, list):
        return None, "rooms 不是数组"
    for item in items:
        # 一条记录必须是 [jsid, jsmc] 两元数组，否则取不出教室身份与展示名。
        if not isinstance(item, list) or len(item) != 2:
            return None, "rooms 里有条目不是两元数组"
    return items, ""


def roster_record(jsid, jsmc, warnings):
    """展开总表的一条两元记录，返回记录字典；缺 jsid 或 jsmc 时返回 None。

    合称记录的 jsid 只记在 source_jsid 上，不按展开出的教室拆成多个 ID；展开失败的展示名
    仍留在记录里，只是进不了不上课结果，原文进警告。
    """
    identity = str(jsid if jsid is not None else "").strip()
    display = str(jsmc if jsmc is not None else "").strip()
    # 缺 jsid 的记录对不上教室身份，缺 jsmc 的没有展示名，都只能跳过。
    if not identity or not display:
        warnings.append("内置教室总表条目缺少 jsid 或 jsmc，已跳过: " + (identity or display or "(空)"))
        return None
    record = expand_record(identity, display)
    # 展开不出房号的展示名不能进不上课结果，原文进警告。
    if not record["expanded"]:
        warnings.append("教室名无法展开，不进不上课结果: " + display)
    return record


def parse_roster(payload):
    """把内置总表对象展开成与既有字典解析一致的形状。

    顶层不是对象、rooms 不是数组或条目不是两元数组时返回空记录集并记一条警告：这种快照不能
    当教室全集，全集退化成「本学年各学期课表首格展开出的教室名」。max_row 只是抓取口径。
    """
    items, reason = roster_items(payload)
    # 形状不符时整份总表都不可信，返回空记录集加警告，让查询继续。
    if items is None:
        return empty_roster(reason)
    max_row = payload.get("max_row")
    # max_row 只作记录用，不是整数时记 0。
    if not isinstance(max_row, int):
        max_row = 0
    records = []
    warnings = []
    for jsid, jsmc in items:
        record = roster_record(jsid, jsmc, warnings)
        # 两元数组里取不出 jsid 或 jsmc 的条目跳过，警告已在那条记录里记下。
        if record is None:
            continue
        records.append(record)
    return {
        "max_row": max_row,
        "captured_at": str(payload.get("captured_at") or ""),
        "records": records,
        "warnings": warnings,
    }


def load_roster(path=None):
    """读内置教室总表数据文件，返回与字典解析一致的形状。

    文件缺失、读不动、JSON 不合法或结构不符时返回空记录集加一条警告：全集退化为「本学年各
    学期课表首格展开出的教室名」，jsid 与全年无课计数不可用，查询照常继续。函数不发请求。
    """
    payload, reason = read_roster_json(path if path else ROSTER_PATH)
    # 文件读不出来时没有可展开的内容，直接给空全集。
    if reason:
        return empty_roster(reason)
    return parse_roster(payload)


def roster_cache_fields(roster):
    """取结果里的 cache.roster 字段：抓取时间、记录数与展开后的单体教室数。"""
    return {
        "captured_at": str(roster.get("captured_at") or ""),
        "record_count": len(roster.get("records") or ()),
        "room_count": len(dictionary_room_index(roster)),
    }


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


# 反推层：把内置总表、本学年各学期教室名与本次课表拼成不上课结果，全部是纯函数。

# 结果里的状态取值与面向用户的转述：只说明该时段不上课，不代表可以占用。
ROOM_STATUS = "no_class"
ROOM_STATUS_TEXT = "不上课"

# 成功结果必带的两句说明，count 为 0 时也要带上。
LIMITATION_TEXT = "这些教室只是该时段不上课，无法获知是否被借用或锁定。"
BLOCK_NOTE_TEXT = "节次按大节判断：块内小节共用一格，任一小节有排课即视为该大节有课。"
# 同名多条总表记录的结果带上这句警告：这类教室的 jsid 取不出唯一值。
MERGED_ROOM_WARNING = "同名行已合并: "

# 时段划分固定按整天口径，与用户选的大节范围无关。
MORNING_BLOCKS = ("0102", "030405")
AFTERNOON_BLOCKS = ("0607", "0809")
EVENING_BLOCKS = ("101112",)

# 空闲过滤开关名与结果字段同名，CLI 的 --free-* 开关转成这几个名字后取交集。
FREE_SWITCHES = ("free_all_day", "free_morning", "free_afternoon", "free_evening")

# 星期几的中文数字：对外展示用「星期三」这类写法，不归一成「周三」。
WEEKDAY_NUMERALS = "一二三四五六日"

# 停止反推的两种情形各自的提示：目标学期名单不完整，本学年缺完整秋春。
SEMESTER_INCOMPLETE_HINT = "请稍后重试；该学期教室名单不完整时无法判断哪些教室不上课"
YEAR_EVIDENCE_HINT = "本学年缺少完整的秋季或春季课表，暂时无法判断全年无课"


def weekday_label(weekday):
    """取星期几的中文名，形如「星期三」；星期越界时返回空串。"""
    index = parse_bound(weekday, *WEEKDAY_RANGE)
    # 星期越界时取不出对应的中文数字，按空名处理。
    if index is None:
        return ""
    return "星期" + WEEKDAY_NUMERALS[index - 1]


def dictionary_room_index(roster):
    """把总表记录摊平成 单体教室 → 来源信息，返回 {教室名: jsid/source_names/merged}。

    一间教室可能由多条总表记录展开而来（同名多条），此时 jsid 不再是唯一身份，置空并在结果里
    记「同名行已合并」警告；展开失败的记录不产生单体教室，它的警告已由解析阶段记下。
    """
    index = {}
    for record in roster.get("records") or ():
        rooms = record.get("rooms") or ()
        # 展开不出房号的记录不产生单体教室，它只能进警告。
        if not rooms:
            continue
        jsmc = str(record.get("jsmc") or "")
        jsid = str(record.get("jsid") or "")
        for name in rooms:
            entry = index.get(name)
            # 第一次见到这间教室时按这条记录建来源信息。
            if entry is None:
                index[name] = {"jsid": jsid, "source_names": [jsmc], "merged": False}
                continue
            # 再次见到同名教室说明总表里有同名多条记录，jsid 不再是唯一身份。
            entry["jsid"] = ""
            entry["merged"] = True
            # 来源展示名按出现顺序记全，同一个展示名只记一次。
            if jsmc not in entry["source_names"]:
                entry["source_names"].append(jsmc)
    return index


def semester_evidence_rooms(semester_rooms):
    """取本学年完整学期的教室名并集，返回 (并集, 完整学期列表)。

    低于完整阈值的学期名单不完整：既不证明有排课，也不证明全年无课，整份跳过不参与。
    """
    evidence = set()
    complete = []
    for semester in sorted(semester_rooms or {}):
        rooms = semester_rooms[semester] or ()
        # 教室名数低于阈值说明该学期名单不完整，不能当成全年排课的证据。
        if not semester_complete(semester, len(rooms)):
            continue
        complete.append(semester)
        for name in rooms:
            evidence.add(str(name))
    return evidence, complete


def has_year_evidence(complete_semesters):
    """判断完整学期里有没有秋季或春季：都没有时全年无课判断缺少可用数据。"""
    for semester in complete_semesters or ():
        rank = parse_semester(semester)
        # 只有完整的秋（1）与春（2）能当全年证据，夏季开课少，单独一份不够。
        if rank is not None and rank[1] in (1, 2):
            return True
    return False


def schedule_room_names(semester_rooms):
    """取本学年各学期课表首格展开出的单体教室名集合。"""
    names = set()
    for rooms in (semester_rooms or {}).values():
        for name in rooms or ():
            names.add(str(name))
    return names


def room_universe_index(roster, semester_rooms, observed_rooms=None, response_names=()):
    """取 教室名 → 来源信息 的索引：总表展开出的教室，加上课表首格见过的教室。

    课表来源有三份：本学年各学期缓存的教室名、缓存目录里所有学期（不限本学年、不限是否过期）的本地
    累计，与本次查询课表首格展开出的教室名；后两份只扩大全集（影响全年无课计数），不参与候选集。总表
    里的教室带 jsid 与来源展示名；只在课表里出现的教室 jsid 为空，来源展示名就是它自己。取并集是因为
    总表是快照，学校新加的教室会先出现在课表里，不并进来就会静默漏报。
    """
    index = dictionary_room_index(roster)
    names = set(response_names or ())
    names |= schedule_room_names(semester_rooms)
    names |= schedule_room_names(observed_rooms)
    for name in names:
        # 总表里已经有这间教室时不覆盖它的 jsid 与来源展示名。
        if name in index:
            continue
        index[name] = {"jsid": "", "source_names": [name], "merged": False}
    return index


def candidate_rooms(universe, evidence):
    """候选集：全集里在本学年某个完整学期数据行首格出现过的单体教室。"""
    # 只留出现在完整学期首格里的教室，其他名字不当候选。
    return {name for name in universe if name in evidence}


def year_round_idle_rooms(universe, candidates):
    """全年无课集：全集里不属于候选集的单体教室，与候选集不相交。"""
    return {name for name in universe if name not in candidates}


def selected_rooms(candidates, keyword):
    """指定教室集：关键词为空时等于候选集，非空时按规范化展示名做包含匹配。

    只在候选集里筛，不创造总表与课表首格之外的教室；没有命中时返回空集，调用方仍按成功处理。
    """
    text = normalize_room_name(keyword)
    # 关键词为空时不缩小范围，指定教室集就是候选集。
    if not text:
        return set(candidates)
    return {name for name in candidates if text in normalize_room_name(name)}


def occupied_rooms(parsed, weekday, blocks):
    """占用集：数据行首格给出的、所选大节在查询日至少有一个占用格的单体教室。

    同行别的块有内容但所选大节为空时不算占用：占用判定只看所选大节在查询日的列。合称行按
    展开后的每一间计入。
    """
    occupied = set()
    for row in parsed.get("rows") or ():
        # 所选大节在这一天没有占用格时这一行不算占用，即使别的块有内容。
        if not any_occupied(row["occupancy"], weekday, blocks):
            continue
        for name in row["rooms"]:
            occupied.add(name)
    return occupied


def section_free(free_blocks, section_blocks):
    """判断某个时段是否整段空闲：时段里每一块都没课才算空闲。"""
    for name in section_blocks:
        # 时段里有一块有课，这个时段就不算空闲。
        if name not in free_blocks:
            return False
    return True


def last_free_period(free_blocks):
    """取空闲大节里最后一块的末小节；没有空闲大节时为 0，全天空闲时为 12。"""
    last = 0
    for name, periods in PERIOD_BLOCKS:
        # 只有空闲的块才把末小节往上抬，按表头顺序走完留下的就是最晚可用节次。
        if name in free_blocks:
            last = periods[-1]
    return last


def room_free_info(occupancy, weekday):
    """算一间教室查询日的空闲信息：空闲大节、占用大节、三个时段与最晚可用节次。

    占用位缺失（课表里没有这间教室的行）时 5 个块都空闲；空闲只表示没有排课，不表示可以
    占用。返回的字段与结果里的教室字段同名，都按查询那一天算。
    """
    free = free_blocks_of_day(occupancy, weekday)
    occupied = [name for name, _periods in PERIOD_BLOCKS if name not in free]
    return {
        "free_all_day": not occupied,
        "free_morning": section_free(free, MORNING_BLOCKS),
        "free_afternoon": section_free(free, AFTERNOON_BLOCKS),
        "free_evening": section_free(free, EVENING_BLOCKS),
        "free_blocks": free,
        "occupied_blocks": occupied,
        "last_free_period": last_free_period(free),
    }


def merged_warnings(*groups):
    """把几组警告合并成一份，重复的只留一条，顺序按来源先后。"""
    warnings = []
    for group in groups:
        for text in group or ():
            # 同一句警告可能来自总表与课表两处，结果里只留一条。
            if text not in warnings:
                warnings.append(text)
    return warnings


def merged_room_warnings(names, index):
    """取同名多条总表记录的教室的合并警告：这些教室的 jsid 取不出唯一值。"""
    warnings = []
    for name in sorted(names):
        # 只有 merged 为真的教室才对应多条总表记录；别的教室 jsid 取不出唯一值是因为它只出现在课表里。
        if not (index.get(name) or {}).get("merged"):
            continue
        warnings.append(MERGED_ROOM_WARNING + name)
    return warnings


def result_room(name, entry, free_info):
    """组装一间教室的结果：展示名、jsid、状态、空闲信息与来源展示名。"""
    payload = {
        "name": name,
        "jsid": entry["jsid"],
        "status": ROOM_STATUS,
        "status_text": ROOM_STATUS_TEXT,
    }
    payload.update(free_info)
    payload["source_names"] = list(entry["source_names"])
    return payload


def empty_room_results(names, index, occupancy_map, weekday):
    """把不上课教室名拼成结果列表：按展示名排序，每间带上它的空闲信息。"""
    rooms = []
    for name in sorted(names):
        # 占用位缺失（这周这天没有它的行）时按 5 个块全空闲处理。
        info = room_free_info(occupancy_map.get(name) or {}, weekday)
        rooms.append(result_room(name, index[name], info))
    return rooms


def reverse_room_sets(roster, semester_rooms, parsed, params, observed_rooms=None):
    """反推候选集、指定教室集、占用集与全年无课集，返回 (集合, 失败结果)。

    集合含 index、blocks、candidates、year_round_idle、selected、occupied 与 occupancy_map。
    semester_rooms 只是本学年各学期的教室名，observed_rooms 是缓存目录里所有学期的本地累计，本次课表
    首格展开出的教室名算第三份课表来源；后两份只参与全集与全年无课计数，不参与候选集，所以往期教室不会
    进结果。目标学期名单不完整，或本学年既没有完整秋季也没有完整春季时返回失败结果：这两种情况下缺失的
    行都不能被解释成不上课。
    """
    blocks = query_blocks(params["period_start"], params["period_end"])
    # 大节范围取不出覆盖块时判定不出占用，属于调用顺序错误。
    if not blocks:
        return None, failure("jwxt", "大节范围取不出覆盖块，无法判定占用", PARAM_HINT)
    semester = str(params["semester"])
    # 全集三来源：内置快照、本学年学期缓存与本地累计、本次课表首格；后两者只扩大全集与无课计数。
    response_names = parsed_room_names(parsed)
    index = room_universe_index(roster, semester_rooms, observed_rooms, response_names)
    evidence, complete = semester_evidence_rooms(semester_rooms)
    target_rooms = (semester_rooms or {}).get(semester) or ()
    # 目标学期名单低于完整阈值时停止反推：缺失的行不能被解释成不上课。
    if not semester_complete(semester, len(target_rooms)):
        message = "目标学期教室名数低于完整阈值，已停止反推: semester=" + semester
        return None, failure("jwxt", message, SEMESTER_INCOMPLETE_HINT)
    # 本学年没有完整秋春时全年无课判断缺少可用数据，同样停止反推。
    if not has_year_evidence(complete):
        return None, failure("jwxt", "本学年没有完整的秋季或春季课表，已停止反推", YEAR_EVIDENCE_HINT)
    universe = set(index)
    candidates = candidate_rooms(universe, evidence)
    return {
        "index": index,
        "blocks": blocks,
        "candidates": candidates,
        "year_round_idle": year_round_idle_rooms(universe, candidates),
        "selected": selected_rooms(candidates, params["keyword"]),
        "occupied": occupied_rooms(parsed, params["weekday"], blocks),
        "occupancy_map": room_occupancy_map(parsed),
    }, None


def empty_classroom_result(roster, semester_rooms, parsed, params, cache=None, observed_rooms=None):
    """反推不上课教室并组装完整结果信封。

    roster 是 load_roster 的结果（沿用既有字典解析的形状），semester_rooms 是 学期 → 该学期
    展开出的教室名列表，parsed 是本次课表的解析结果，params 是 validate_query 通过的参数，
    cache 由调用方（编排）传入，默认空字典，observed_rooms 是缓存目录里所有学期的本地累计教室名
    （只扩大全集）。函数不读时钟、不发请求、不读写缓存：结果集由指定教室集减去占用集得到，因此
    这周这天整天没课、不出现在响应里的教室照样进结果。目标学期不完整，或本学年没有完整秋春时返回
    失败结果，且不含 rooms。
    """
    sets, error = reverse_room_sets(roster, semester_rooms, parsed, params, observed_rooms)
    # 停止反推的两种情形由 reverse_room_sets 说明，失败结果里没有 rooms。
    if error is not None:
        return error
    weekday = params["weekday"]
    resting = sets["selected"] - sets["occupied"]
    rooms = empty_room_results(resting, sets["index"], sets["occupancy_map"], weekday)
    warnings = merged_warnings(
        roster.get("warnings"),
        parsed.get("warnings"),
        merged_room_warnings(resting, sets["index"]),
    )
    fields = {
        "semester": str(params["semester"]),
        "week_start": params["week_start"],
        "week_end": params["week_end"],
        "weekday": weekday,
        "weekday_name": weekday_label(weekday),
        "period_start": params["period_start"],
        "period_end": params["period_end"],
        "blocks": sets["blocks"],
        "keyword": params["keyword"],
        "skjs": keyword_skjs(params["keyword"], roster.get("records") or ()),
        "limitation": LIMITATION_TEXT,
        "block_note": BLOCK_NOTE_TEXT,
        "excluded_year_round_idle_count": len(sets["year_round_idle"]),
        "count": len(rooms),
        "rooms": rooms,
        "cache": cache if cache is not None else {},
        "warnings": warnings,
    }
    return success("jwxt", fields)


def filter_free_rooms(rooms, switches):
    """按已打开的空闲开关过滤结果列表。

    开关名与结果字段同名，同时给多个开关时取交集（上午 + 晚上就是两者都空闲）；函数只筛结果，
    不改写任何字段，调用方按过滤后的列表重算 count。
    """
    opened = [name for name in FREE_SWITCHES if name in set(switches or ())]
    # 一个开关都没打开时结果原样返回，顺序也保持。
    if not opened:
        return list(rooms or ())
    kept = []
    for room in rooms or ():
        # 已打开的开关里任一字段为假说明这间教室不满足条件，全部为真才留下。
        if all(room.get(name, False) for name in opened):
            kept.append(room)
    return kept
# 缓存层：只把解析成功的学期教室名写盘，读不出内容时按没有缓存处理。


def cache_dir():
    """取缓存目录；环境变量改的是目录，不是文件名。

    QFNU_CLASSROOM_CACHE_PATH 指定整目录，各学期的教室名文件都落在该目录；没给环境变量时
    用状态目录下的 classroom-schedule 子目录。
    """
    value = os.environ.get(CACHE_ENV_VAR, "").strip()
    # 指定了环境变量就整目录用它，文件名仍由本模块决定。
    if value:
        return expand_path(value)
    return os.path.join(state_dir(), CACHE_DIR_NAME)


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
    刷新这份缓存。学期缓存用它判断新鲜度。
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


def local_cache_semesters():
    """列出缓存目录里所有学期缓存对应的学期值，返回排序去重后的列表。

    只认文件名形如 <学期>.json 且学期格式合法的普通文件：dictionary.json、随机名、别的后缀与
    「全部」这类非学期值都不是学期缓存。目录还不存在（第一次查询）时没有本地累计，返回空列表。
    """
    directory = cache_dir()
    try:
        names = os.listdir(directory)
    except OSError:
        # 缓存目录还不存在时列不出任何文件，按没有本地累计处理。
        return []
    found = []
    # 逐个文件名判断它是不是学期缓存：只有合法学期值加 .json 的普通文件才算数。
    for name in names:
        # 后缀不是 .json 的文件名不是学期缓存，跳过。
        if not name.endswith(SEMESTER_CACHE_SUFFIX):
            continue
        semester = name[: -len(SEMESTER_CACHE_SUFFIX)]
        # 学期格式不合法的文件名取不出学期值，不能当本地累计里的一份缓存。
        if parse_semester(semester) is None:
            continue
        # 同名目录不是缓存文件，里面读不出教室名。
        if not os.path.isfile(os.path.join(directory, name)):
            continue
        found.append(semester)
    return sorted(set(found))


def local_cache_rooms(semester, payload):
    """取一份学期缓存里的教室名列表，返回 (教室名列表, 失败说明)。

    内容不合法（读不出、rooms 不是数组、文件里的学期与文件名不一致）时返回 (None, 原因)：这类文件
    不能当成该学期的教室名，调用方跳过它并记一条警告。
    """
    # 文件读不出或不是对象时取不出任何教室名。
    if payload is None:
        return None, "读不出或不是对象"
    rooms = payload.get("rooms")
    # rooms 不是数组说明这份缓存的字段不全，不能拿它当教室名列表。
    if not isinstance(rooms, list):
        return None, "rooms 不是数组"
    # 文件里的学期与文件名不一致说明文件被换过，取不出可信的学期归属。
    if str(payload.get("semester") or "") != semester:
        return None, "文件里的学期与文件名不一致"
    return [str(name) for name in rooms], ""


def read_local_semester_rooms():
    """读缓存目录里所有学期（不限本学年、不限是否过期）缓存到的教室名。

    返回 (学期 → 教室名列表, 警告列表)。内容不合法的文件跳过并记一条警告，不抛异常：本地累计只用
    来扩大全集与全年无课计数，个别文件坏掉不影响结果对不对。这些文件是长期资产，过期只表示该重新
    拉取，这里不删任何文件。
    """
    rooms_by_semester = {}
    warnings = []
    # 每个学期文件各读一次，坏掉的跳过并记警告，其他学期的教室名照样凑进本地累计。
    for semester in local_cache_semesters():
        rooms, reason = local_cache_rooms(semester, read_cache_file(semester_cache_path(semester)))
        # 内容不合法时跳过这份缓存：少一个学期的教室名只让全集小一点，不必让查询失败。
        if rooms is None:
            warnings.append(LOCAL_CACHE_BAD_WARNING + semester + "（" + reason + "）")
            continue
        rooms_by_semester[semester] = rooms
    return rooms_by_semester, warnings


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


# 请求层：只发学生端教室课表的两个只读请求，传输被掐断时最多再重发两次。


def page_headers(referer):
    """父页 GET 的请求头：Referer 指向教务侧栏，UA 用教室接口的桌面版标识。

    这两个请求只复用现有会话的 Cookie，不改 jwxt_client 的全局 USER_AGENT。
    """
    return {"Referer": referer, "User-Agent": CLASSROOM_USER_AGENT}


def post_headers(referer):
    """两个 POST 的请求头：表单编码、父页 Referer，以及同一个桌面版标识。"""
    return {
        "Content-Type": FORM_CONTENT_TYPE,
        "Referer": referer,
        "User-Agent": CLASSROOM_USER_AGENT,
    }


def send_request(client, method, url, form, referer):
    """发一次只读请求，返回 (正文, 传输失败说明)。

    form 为 None 时是 GET，不带正文；否则按表单编码发 POST。传输被掐断（分块传输中断、连接
    被重置）或状态不是 200 时正文置空：半截正文既判不了课表，也不能写缓存。
    """
    headers = page_headers(referer) if form is None else post_headers(referer)
    body = None if form is None else urlencode(form).encode("utf-8")
    try:
        status, _final_url, raw = client.text(method, url, body, headers)
    except (http.client.HTTPException, OSError) as exc:
        # 读正文中途断线：这次正文没传完，交给上层丢掉再请求。
        return "", "传输中断: " + str(exc)
    # 非 200 说明这次没拿到页面，例如会话失效时的跳转响应。
    if status != 200:
        return "", "HTTP 状态不是 200: " + str(status)
    return raw, ""


def retry_failure(error, attempts):
    """把最后一次失败的说明补上请求次数，让用户知道已经重发过。"""
    message = str(error.get("error") or "") + "；已请求 " + str(attempts) + " 次"
    return failure("jwxt", message, str(error.get("hint") or ""))


def incomplete_grid_body(raw):
    """判断一份没通过解析的网格正文是不是「正文不完整」，值得丢弃这次正文重发。

    完整错误页——登录页、非法访问、会话互踢、查询节次出错——都能完整取到，重发只会拿到同一份；
    其余情况都没形成可用课表（缺闭合的 table#kbtable、表头不符、格数不是 35、一行首格都取不出
    教室名），按正文不完整处理：调用方丢弃这份正文重发，最多再请求 2 次。
    """
    # 命中错误标记的正文是完整页面，重发拿不到别的东西。
    if page_failure(raw) is not None:
        return False
    # 该学期没配节次是教务侧状态，重发也是一样的页面；其余情况都没形成可用课表。
    return PERIOD_ERROR_TEXT not in raw


def incomplete_failure(error, attempts):
    """正文连续不完整时的失败结果：口径是响应不完整，不说成接口契约变了。"""
    reason = str(error.get("error") or "")
    message = INCOMPLETE_TEXT + ": " + reason + "；已请求 " + str(attempts) + " 次"
    return failure("jwxt", message, INCOMPLETE_HINT)


def request_with_retry(client, method, url, form, referer, parse, parse_args=(), incomplete=None):
    """按次数上限请求一条只读接口，返回 (取值, 失败结果)。

    parse 接收正文与 parse_args，返回 (取值, 失败结果)：取值非 None 表示这次正文可用。重发只发生在
    两类情况：传输被掐断或状态不是 200；incomplete 判定这份正文没形成可用课表（正文不完整）。完整
    错误页由 incomplete 返回 False 表达，一次即停；父页不传 incomplete，标题不符同样一次即停。
    """
    error = None
    body_incomplete = False
    for _attempt in range(FETCH_ATTEMPTS):
        raw, transport = send_request(client, method, url, form, referer)
        # 传输被掐断时没有可判定的正文，按这次失败重发。
        if transport:
            error = failure("jwxt", transport, TRANSPORT_HINT)
            body_incomplete = False
            continue
        value, error = parse(raw, *parse_args)
        # 正文可用时立刻返回，不再重发。
        if value is not None:
            return value, None
        # 完整错误页（登录页、非法访问、会话互踢、节次出错）与父页标题不符都一次即停。
        if incomplete is None or not incomplete(raw):
            return None, error
        # 正文不完整：丢掉这次正文重发，次数用完时按响应不完整报错。
        body_incomplete = True
    # 次数用完：正文一直不完整与传输一直被掐断按各自口径说明。
    if body_incomplete:
        return None, incomplete_failure(error, FETCH_ATTEMPTS)
    return None, retry_failure(error, FETCH_ATTEMPTS)


def semester_form(semester, kbjcmsid):
    """学期教室名 POST 的表单：教室与时间参数全空，只按学期取该学期全部大节。

    skjsid 服务端不认；xqid 留空让三个校区一起返回。
    """
    return {
        "xnxqh": str(semester if semester is not None else "").strip(),
        "kbjcmsid": str(kbjcmsid if kbjcmsid is not None else "").strip(),
        "skyx": "",
        "xqid": "",
        "jzwid": "",
        "skjsid": "",
        "skjs": "",
        "zc1": "",
        "zc2": "",
        "skxq1": "",
        "skxq2": "",
        "jc1": "",
        "jc2": "",
    }


def query_form(semester, kbjcmsid, week_start, week_end, weekday, skjs=""):
    """本次查询 POST 的表单：周次与星期交给服务端过滤，节次参数留空。

    jc1 与 jc2 一旦传了就只剩所选大节的列，全天空闲与最晚可用节次都算不出来，因此恒为空。
    """
    form = semester_form(semester, kbjcmsid)
    form["skjs"] = str(skjs if skjs is not None else "").strip()
    form["zc1"] = str(week_start)
    form["zc2"] = str(week_end)
    # 星期顶死一天：起止两值写同一个星期几，免得把多天的格混在一次响应里。
    form["skxq1"] = str(weekday)
    form["skxq2"] = str(weekday)
    return form


def keyword_skjs(keyword, records):
    """取本次课表 POST 要传的 skjs：只有精确教室名才传，楼名等包含匹配传空串。

    关键词与内置总表里某条未展开 jsmc 规范化后完全相同时，传该条未展开的原名；部分关键词直接
    发给服务端的行为没有实测过，所以关键词只在本地按展示名过滤。
    """
    text = normalize_room_name(keyword)
    # 空关键词不缩小范围，skjs 留空。
    if not text:
        return ""
    for record in records or ():
        # 非总表记录取不出 jsmc，跳过。
        if not isinstance(record, dict):
            continue
        original = str(record.get("jsmc") or "")
        # 规范化后完全相同才算精确教室名。
        if normalize_room_name(original) == text:
            # 写入未展开的原名，不用展开后的单体教室名。
            return original
    return ""


# 进程内串行刷新状态：资源名 → 锁，资源名 → 这次刷新等到的结果。
SERIAL_GUARD = threading.Lock()
SERIAL_LOCKS = {}
SERIAL_RESULTS = {}


def semester_resource(semester):
    """取某学期在串行刷新里的资源名：学期值不同的资源互不等待。"""
    return SEMESTER_RESOURCE_PREFIX + str(semester if semester is not None else "").strip()


def serial_lock(name):
    """取资源名对应的锁；同一资源名在进程里只有同一个锁对象。"""
    with SERIAL_GUARD:
        lock = SERIAL_LOCKS.get(name)
        # 第一次用到这个资源时建锁，之后的调用都用它等同一次刷新。
        if lock is None:
            lock = threading.Lock()
            SERIAL_LOCKS[name] = lock
        return lock


def serial_refresh(name, fetch, *args):
    """按资源名串行刷新，返回 (取值, 失败结果)。

    同一资源名在进程里只会真正请求一次：后来的调用者拿到锁后直接复用这次结果，不会并行对
    同一学期或字典重复 POST。失败的结果同样复用，避免同一进程反复重试。fetch 是发请求的
    函数，args 是它的参数。
    """
    lock = serial_lock(name)
    with lock:
        # 这个资源本进程已经刷过一次时直接复用，不再重复请求上游。
        if name in SERIAL_RESULTS:
            return SERIAL_RESULTS[name]
        result = fetch(*args)
        SERIAL_RESULTS[name] = result
        return result


def reset_serial_refresh():
    """清空进程内的刷新记录：一次查询开始前调用，让本次查询自己去刷新资源。

    缓存文件本身仍然生效，这里清掉的只是本进程已经等到的同一次结果，供编排与测试隔离。
    """
    with SERIAL_GUARD:
        SERIAL_RESULTS.clear()


def request_parent_page(client):
    """真正读父页的那一次：GET 父页，Referer 用教务侧栏入口。

    父页只有十几 KB，标题不符的语义是「页面未被识别」，不按正文不完整重发，因此不传 incomplete。
    """
    return request_with_retry(
        client,
        "GET",
        CLASSROOM_PAGE_URL,
        None,
        MAIN_URL,
        parse_classroom_page,
    )


def fetch_parent_page(client):
    """读教室课表父页，返回 (页面信息, 失败结果)。

    页面不可用（登录页、会话互踢、非法访问、标题不符）时返回失败结果，调用方不得继续发请求；
    同一个进程里这条 GET 只发一次。
    """
    return serial_refresh(CLASSROOM_PAGE_RESOURCE, request_parent_page, client)


def parse_semester_page(raw, semester, kbjcmsid):
    """判定学期课表正文并写缓存，返回 (缓存内容, 失败结果)。

    只有一个功能：把正文放在第一个参数上，好让 request_with_retry 用统一的
    「(正文, 其余参数)」调用约定；正文不可用时整份弃用，不落盘。
    """
    return write_semester_cache_from_page(semester, kbjcmsid, raw)


def request_semester_page(client, semester, kbjcmsid):
    """真正拉学期课表的那一次：时间参数全空，正文通过完整性校验时才写缓存。

    网格 POST 按「正文不完整就重发」处理；错误页仍一次即停。
    """
    return request_with_retry(
        client,
        "POST",
        CLASSROOM_IFR_URL,
        semester_form(semester, kbjcmsid),
        CLASSROOM_REFERER,
        parse_semester_page,
        (semester, kbjcmsid),
        incomplete=incomplete_grid_body,
    )


def fetch_semester_page(client, semester, kbjcmsid):
    """拉一次某学期有排课的教室名并写入缓存，返回 (缓存内容, 失败结果)。

    取该学期全部 5 个大节，只统计数据行首格；同一个学期在进程里只请求一次。
    """
    return serial_refresh(semester_resource(semester), request_semester_page, client, semester, kbjcmsid)


def fetch_query_page(client, semester, kbjcmsid, week_start, week_end, weekday, skjs=""):
    """发本次查询的课表 POST，返回 (解析结果, 失败结果)。

    周次范围写进 zc1/zc2，同一天写进 skxq1/skxq2，jc1/jc2 留空以取整天 35 格；这份带时间
    参数的正文不写学期缓存。
    """
    form = query_form(semester, kbjcmsid, week_start, week_end, weekday, skjs)
    return request_with_retry(
        client,
        "POST",
        CLASSROOM_IFR_URL,
        form,
        CLASSROOM_REFERER,
        parse_classroom_table,
        incomplete=incomplete_grid_body,
    )


# 编排层：按固定顺序刷新缓存并只发一次带周次与星期的课表，再交给反推层组装结果。

# 目标学期不可用时只能改用本学年下拉里的学期，继续拉其他学期也查不出这一个。
TARGET_SEMESTER_HINT = "请改用父页下拉里本学年的学期后重试"
# 本学年学期没有可用缓存时的提示：全年无课名单不完整，不能把缺失的行当成不上课。
SEMESTER_CACHE_HINT = "请稍后重试；本学年学期教室名单不完整时无法判断哪些教室全年无课"

# 学期教室名的三种来源：直接用未过期缓存、这次刷新成功、刷新失败退回未过期的旧缓存。
SEMESTER_FROM_CACHE = "cache"
SEMESTER_REFRESHED = "refreshed"
SEMESTER_STALE = "stale"


def cache_usable(payload, now):
    """判断缓存内容还能不能用：有内容且写入时间未超过 7 日。

    正好满 7 日仍算可用，过期缓存不算可用：调用方会去刷新这份缓存。时间戳读不出时同样算不可用。
    """
    # 读不出缓存内容（文件缺失或损坏）时没有可用缓存。
    if payload is None:
        return False
    return not cache_expired(payload.get("fetched_at"), now)


def session_lost(error):
    """判断失败是不是会话失效：登录页与互踢提示都只能重新登录，改参数没用。"""
    text = str(error.get("error") or "")
    # 正文是登录页或互踢提示时本次会话已经不可用，后面的 POST 拿不到别的东西。
    return ("登录页" in text) or (SESSION_KICKED_TEXT in text)


def query_plan(page, params):
    """定下本次查询要用的本学年学期列表，返回 (学期列表, 警告, 失败结果)。

    只取目标学年：其他学年（含未来学年）的项不进列表，也不会为它们发 POST。目标学期不在本学年
    列表里时在这里停下，调用方不会再发任何请求。
    """
    semesters, warnings = year_semester_list(page["semesters"], params["semester"])
    # 本学年一个学期都取不出时推不出全年无课名单，直接停下。
    if semesters is None:
        message = "父页下拉里没有本学年学期，无法使用该学期: semester=" + str(params["semester"])
        return None, warnings, failure("jwxt", message, TARGET_SEMESTER_HINT)
    # 目标学期不在本学年列表里时停在这里：继续拉其他学期也查不出这一个。
    if params["semester"] not in semesters:
        picked = str(params["semester"])
        message = "目标学期不属于本学年或不在父页下拉中，无法使用该学期: semester=" + picked
        return None, warnings, failure("jwxt", message, TARGET_SEMESTER_HINT)
    return semesters, warnings, None


def semester_rooms_for(client, semester, kbjcmsid, now):
    """取某学期有排课的教室名，返回 (教室名列表, 来源, 失败结果)。

    缓存缺失或早于 7 日时刷新：正文通过完整性校验时会顺带写盘，来源记 refreshed。刷新失败时
    再看一眼缓存，未过期的旧缓存可以继续用于全年无课判断，来源记 stale；过期缓存不算可用，
    没有可用缓存时返回失败结果。会话中途失效时直接返回该失败结果，调用方必须停止后续 POST。
    """
    cached = read_semester_cache(semester)
    # 缓存还在 7 日内时直接用它的教室名，不必再发这个学期的课表请求。
    if cache_usable(cached, now):
        return list(cached["rooms"]), SEMESTER_FROM_CACHE, None
    fresh, error = fetch_semester_page(client, semester, kbjcmsid)
    # 刷新成功时正文已经通过完整性校验并写盘，这份教室名就是该学期的名单。
    if error is None:
        return list(fresh["rooms"]), SEMESTER_REFRESHED, None
    # 会话失效与参数无关，换缓存也救不回来，立刻把失败交给调用方去停下。
    if session_lost(error):
        return None, SEMESTER_FROM_CACHE, error
    # 刷新失败后重新读一次缓存文件：这期间别的进程可能已经刷新好这个学期。
    again = read_semester_cache(semester)
    # 未过期的旧缓存可以继续用于全年无课判断，但要记下这个学期这次没刷新成功。
    if cache_usable(again, now):
        return list(again["rooms"]), SEMESTER_STALE, None
    # 过期缓存不算可用：这个学期的缺失行不能被解释成不上课，只能停下。
    message = "本学年学期没有可用缓存，全年无课名单不完整: semester=" + str(semester)
    if error.get("error"):
        message = message + "；刷新失败: " + str(error["error"])
    return None, SEMESTER_FROM_CACHE, failure("jwxt", message, SEMESTER_CACHE_HINT)


def collect_year_rooms(client, semesters, kbjcmsid, now):
    """补齐本学年学期缓存，返回 (学期 → 教室名列表, 刷新过的学期, 用了旧缓存的学期, 失败结果)。

    只拉传进来的本学年学期，不碰其他学年。某个学期没有可用缓存或中途会话失效时返回失败结果：
    这两种情况下全年无课名单都不完整，缺失的行不能被解释成不上课。
    """
    rooms_by_semester = {}
    refreshed = []
    stale = []
    for semester in semesters:
        rooms, source, error = semester_rooms_for(client, semester, kbjcmsid, now)
        # 会话失效或没有可用缓存时立刻停下，已经通过校验并写盘的缓存保持原样。
        if error is not None:
            return None, refreshed, stale, error
        rooms_by_semester[semester] = rooms
        # 只有这次真的刷新成功的学期进 refreshed，用户才知道哪些学期是刚拉的。
        if source == SEMESTER_REFRESHED:
            refreshed.append(semester)
        # 刷新失败但用未过期旧缓存顶上的学期也要标出来，名单不是这次拉的。
        elif source == SEMESTER_STALE:
            stale.append(semester)
    return rooms_by_semester, refreshed, stale, None


def query_page_of(client, params, roster, kbjcmsid):
    """发本次查询的课表 POST，返回 (解析结果, 失败结果)。

    关键词与某条内置总表未展开 jsmc 完全相同时把 skjs 设为该原名，其余情况留空只在本地按展示名
    过滤。周次与星期写进表单、节次留空以取整天 35 格；这份带时间参数的正文不写学期缓存。
    """
    skjs = keyword_skjs(params["keyword"], roster.get("records") or ())
    return fetch_query_page(
        client,
        params["semester"],
        kbjcmsid,
        params["week_start"],
        params["week_end"],
        params["weekday"],
        skjs,
    )


def with_plan_warnings(result, warnings):
    """把本学年学期列表阶段的警告并进结果信封：下拉里格式不符的项要让用户看到。"""
    # 失败结果没有 warnings 字段，也没有教室说明可合并，原样返回。
    if not warnings or not result.get("ok"):
        return result
    result["warnings"] = merged_warnings(warnings, result.get("warnings"))
    return result


def query_empty_classrooms(
    client, semester, week_start, week_end, weekday, period_start, period_end, keyword=""
):
    """查不上课教室：编排参数校验、缓存刷新、门槛判断与本次课表查询，返回结果信封。

    client 是已登录的只读会话（有 text 方法即可），后 7 个参数对应 CLI 的 --semester、--week、
    --week-end、--weekday、--period-start、--period-end 与 --keyword。调用顺序固定：清掉本进程
    上一次等到的刷新结果 → 校验参数 → 读父页 → 载入内置教室总表（本地文件，不发请求）→ 补齐本
    学年学期缓存 → 读缓存目录里所有学期的本地累计 → 发一次带周次与星期的课表（jc 留空）→ 反推
    求差。参数无效时一个上游请求都不发；失败结果沿用 ok=false、error 与 hint 且不含 rooms，成功
    结果的 cache 字段说明本次用了哪些学期、本地累计到哪些学期与总表快照的口径。
    """
    # 本次查询自己去刷新资源，不复用本进程上一次查询等到的结果。
    reset_serial_refresh()
    params, error = validate_query(
        semester, week_start, week_end, weekday, period_start, period_end, keyword
    )
    # 参数无效时连父页都不读，避免把无效参数带进上游请求。
    if error is not None:
        return error
    now = now_moment()
    page, error = fetch_parent_page(client)
    # 父页不可用（登录页、会话互踢、非法访问、标题不符）时不再发任何 POST。
    if error is not None:
        return error
    semesters, warnings, error = query_plan(page, params)
    # 目标学期不在该学年下拉里时停下，一个上游请求都不发。
    if error is not None:
        return error
    # 内置总表是仓库里的数据文件：读它不发请求，读不出来只退化成课表首格全集。
    roster = load_roster()
    rooms_by_semester, refreshed, stale, error = collect_year_rooms(
        client, semesters, page["kbjcmsid"], now
    )
    # 会话中途失效或某个学期没有可用缓存时停下：全年无课名单不完整。
    if error is not None:
        return error
    # 本地累计：缓存目录里所有学期（不限本学年、不限是否过期）的教室名，只扩大全集与全年无课
    # 计数；往期教室不进候选集，所以结果集与有没有它无关。坏文件跳过并记警告，不中断查询。
    local_rooms, local_warnings = read_local_semester_rooms()
    parsed, error = query_page_of(client, params, roster, page["kbjcmsid"])
    # 课表不可用（登录页、互踢、缺表、格数不是 35、节次出错）时失败结果里不含 rooms。
    if error is not None:
        return error
    cache = {
        "semesters": semesters,
        "refreshed_semesters": refreshed,
        "stale_semesters": stale,
        "roster": roster_cache_fields(roster),
        # 本次构成全集的本地累计学期列表，排序去重；与快照口径互不影响。
        "observed_semesters": sorted(local_rooms),
    }
    result = empty_classroom_result(
        roster, rooms_by_semester, parsed, params, cache, local_rooms
    )
    # 本学年下拉的格式问题与本地累计的坏文件都要让用户看到，合并进同一个 warnings 字段。
    return with_plan_warnings(result, warnings + local_warnings)
