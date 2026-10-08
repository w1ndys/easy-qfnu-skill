"""查无上课教室的纯规则：参数校验、学期遍历窗口、节次块与名称规范化。

本模块只放不联网的规则，请求与编排在后续任务里接上。
"""

import re

from .result import failure

# 学期格式：YYYY-YYYY-N，末位 1 秋、2 春、3 夏。
SEMESTER_RE = re.compile(r"^(\d{4})-(\d{4})-([123])$")

# 遍历窗口最多取多少个学期。
SEMESTER_WINDOW_SIZE = 13

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

# 一周 7 天，每天 5 个节次块，课表网格因此固定 35 格。
WEEKDAYS = (1, 2, 3, 4, 5, 6, 7)

# 参数出错时的统一提示，指出这几个参数怎么传。
PARAM_HINT = "使用 --semester、--week、--weekday、--period-start、--period-end 传参数"

# 全角数字与半角数字的对应表，只用于名称规范化。
DIGIT_TRANSLATION = str.maketrans("０１２３４５６７８９", "0123456789")
# 房号：末尾的字母头加数字，可选字母尾缀；它前面的部分算楼栋前缀。
ROOM_NUMBER_RE = re.compile(r"[A-Za-z]*\d+[A-Za-z]?$")

# 房号开头的字母头，用来给「只有数字」的后项补字母。
ROOM_HEAD_RE = re.compile(r"[A-Za-z]*")

# 合称分隔符：顿号、半角句点和空白。
ROOM_SEPARATOR_RE = re.compile(r"[、.\s]+")

# 一个周次表达式：A-B周 是闭区间，A周 是单周；越界或倒置的表达式丢弃。
WEEK_EXPRESSION_RE = re.compile(r"(\d+)(?:\s*-\s*(\d+))?\s*周")

# 一个周次都解析不出时按整学期处理，周次全集的上下界取 WEEK_RANGE。
WEEK_FULL_RANGE = range(WEEK_RANGE[0], WEEK_RANGE[1] + 1)


def parse_semester(value):
    """把学期拆成排序键 (起始年, 末位)。

    格式不是 YYYY-YYYY-N 时返回 None，调用方按无效学期处理。
    """
    match = SEMESTER_RE.match(str(value if value is not None else "").strip())
    # 下拉里可能有「全部」这类非学期项，格式不符一律当无效。
    if match is None:
        return None
    return int(match.group(1)), int(match.group(3))


def semester_window(options, selected, limit=SEMESTER_WINDOW_SIZE):
    """按父页选中学期取遍历窗口，返回 (窗口, 警告)。

    窗口只含不晚于选中学期的最近 limit 项，按新到旧排列；不足 limit 项时全用。
    选中学期缺失或格式不符时窗口为 None，调用方必须拒绝查询。
    """
    warnings = []
    selected_rank = parse_semester(selected)
    # 没有可比对的选中学期，窗口上界无法确定，只能拒绝。
    if selected_rank is None:
        return None, warnings
    ranked = []
    for option in options or ():
        rank = parse_semester(option)
        # 格式不符的下拉项跳过并记警告，不参与窗口比较。
        if rank is None:
            text = str(option if option is not None else "").strip()
            if text:
                warnings.append("学期下拉项格式不符，已跳过: " + text)
            continue
        # 晚于选中学期的项（未来学期或空学期）不进窗口。
        if rank > selected_rank:
            continue
        ranked.append((rank, str(option).strip()))
    ranked.sort(reverse=True)
    window = [value for _rank, value in ranked[:limit]]
    return window, warnings


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


def validate_query(semester, week, weekday, period_start, period_end, keyword=""):
    """校验查询参数，返回 (参数, 失败结果)。

    任一参数无效时返回失败结果并指出参数名，调用方不得再发上游请求。
    """
    semester_text = str(semester if semester is not None else "").strip()
    # 学期格式不对就构造不出课表 POST 的 xnxqh。
    if parse_semester(semester_text) is None:
        return None, failure("jwxt", "学期格式必须是 YYYY-YYYY-N: semester=" + semester_text, PARAM_HINT)
    week_value = parse_bound(week, *WEEK_RANGE)
    # 周次越界就不能交给服务端过滤，直接拒绝。
    if week_value is None:
        return None, failure("jwxt", "周次必须在 1 到 30 之间: week=" + str(week), PARAM_HINT)
    weekday_value = parse_bound(weekday, *WEEKDAY_RANGE)
    # 星期越界同样无法确定要读哪一天的数据。
    if weekday_value is None:
        return None, failure("jwxt", "星期必须在 1 到 7 之间: weekday=" + str(weekday), PARAM_HINT)
    start = parse_bound(period_start, *PERIOD_RANGE)
    # 起始节次越界，查询范围没有意义。
    if start is None:
        return None, failure("jwxt", "起始节次必须在 1 到 12 之间: period_start=" + str(period_start), PARAM_HINT)
    end = parse_bound(period_end, *PERIOD_RANGE)
    # 结束节次越界，查询范围没有意义。
    if end is None:
        return None, failure("jwxt", "结束节次必须在 1 到 12 之间: period_end=" + str(period_end), PARAM_HINT)
    # 起始大于结束时闭区间为空，判不出占用，直接拒绝。
    if start > end:
        message = "起始节次不能大于结束节次: period_start=" + str(start) + ", period_end=" + str(end)
        return None, failure("jwxt", message, PARAM_HINT)
    params = {
        "semester": semester_text,
        "week": week_value,
        "weekday": weekday_value,
        "period_start": start,
        "period_end": end,
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


def query_blocks(period_start, period_end):
    """取与查询闭区间相交的节次块名，按表头顺序返回。"""
    start = parse_bound(period_start, *PERIOD_RANGE)
    end = parse_bound(period_end, *PERIOD_RANGE)
    blocks = []
    # 参数无效或起始大于结束时闭区间为空，没有块进入查询范围。
    if start is None or end is None or start > end:
        return blocks
    for name, periods in PERIOD_BLOCKS:
        # 块内任一小节落在闭区间内，整块都进查询范围。
        if any(start <= period <= end for period in periods):
            blocks.append(name)
    return blocks


def expected_block_names():
    """表头 35 列的块名顺序：星期 1 到 7 各重复 5 个块，共 35 格。"""
    names = []
    for _weekday in WEEKDAYS:
        names.extend(name for name, _periods in PERIOD_BLOCKS)
    return names


def query_cells(period_start, period_end):
    """取查询涉及的表头格，顺序与 35 列表头一致：每天先排完相交块。"""
    cells = []
    blocks = query_blocks(period_start, period_end)
    for weekday in WEEKDAYS:
        for block_name in blocks:
            cells.append(
                {
                    "weekday": weekday,
                    "block": block_name,
                    "periods": list(block_periods(block_name)),
                }
            )
    return cells


def week_qualifier(text):
    """取课程块里的单双限定词，返回「单」「双」或空串。

    单和双同时出现在一个块里时无法判断各自作用的区间，按不加限定处理，宁可当作
    有课，也不漏掉占用。
    """
    content = str(text if text is not None else "")
    odd = "单" in content
    even = "双" in content
    # 两者都出现（或都没有）时不加过滤。
    if odd == even:
        return ""
    return "单" if odd else "双"


def filter_weeks(weeks, qualifier):
    """按限定词过滤周次：单留奇数周，双留偶数周，没有限定词原样返回。"""
    # 单：只留奇数周。
    if qualifier == "单":
        return {week for week in weeks if week % 2 == 1}
    # 双：只留偶数周。
    if qualifier == "双":
        return {week for week in weeks if week % 2 == 0}
    return set(weeks)


def parse_weeks(text):
    """从课程块文本解析周次，返回 (周次列表, 是否一个都没解析出来)。

    `A-B周` 是闭区间，`A周` 是单周，逗号或顿号连接多个表达式取并集；单双限定词
    作用于该块已解析出的周次，块里没有可用区间时作用于 1 到 30。一个周次都解析
    不出来时按 1 到 30 全算有课，并让 weeks_unparsed 为 true。
    """
    weeks = set()
    for match in WEEK_EXPRESSION_RE.finditer(str(text if text is not None else "")):
        start = parse_bound(match.group(1), *WEEK_RANGE)
        end = parse_bound(match.group(2), *WEEK_RANGE) if match.group(2) else start
        # 端点越界或区间倒置的表达式直接丢弃，不猜它想表示哪几周。
        if start is None or end is None or start > end:
            continue
        weeks.update(range(start, end + 1))
    qualifier = week_qualifier(text)
    # 限定词没有可作用的区间时作用于 1 到 30。
    if qualifier:
        weeks = filter_weeks(weeks or set(WEEK_FULL_RANGE), qualifier)
    # 一个周次都没解析出来时按整学期处理，调用方据此保守判断该块有课。
    if not weeks:
        return list(WEEK_FULL_RANGE), True
    return sorted(weeks), False
