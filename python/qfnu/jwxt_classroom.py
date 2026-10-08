"""查无上课教室的纯规则：参数校验、本学年学期列表、节次块与名称规范化。

本模块只放不联网的规则，请求与编排在后续任务里接上。
"""

import re

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
