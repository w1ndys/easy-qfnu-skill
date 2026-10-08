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
