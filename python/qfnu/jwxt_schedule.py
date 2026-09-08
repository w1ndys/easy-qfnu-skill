"""只读学期课表。GET /jsxsd/xskb/xskb_list.do，不提交选课。"""

import html
import re
from urllib.parse import urlencode

from . import trace
from .jwxt_auth import contains_any, strip_tags
from .jwxt_client import SCHEDULE_URL, JWXTError
from .jwxt_html import LOGIN_MARKERS, parse_table_html
from .result import success

KBTABLE_RE = re.compile(r'(?is)<table\b[^>]*\bid=["\']kbtable["\'][^>]*>(.*?)</table\s*>')
DIV_RE = re.compile(r"(?is)<div\b([^>]*)>(.*?)</div\s*>")
WEEKDAY_LABELS = (
    ("星期一", "周一"),
    ("星期二", "周二"),
    ("星期三", "周三"),
    ("星期四", "周四"),
    ("星期五", "周五"),
    ("星期六", "周六"),
    ("星期日", "周日"),
    ("星期天", "周日"),
    ("周一", "周一"),
    ("周二", "周二"),
    ("周三", "周三"),
    ("周四", "周四"),
    ("周五", "周五"),
    ("周六", "周六"),
    ("周日", "周日"),
)


def kbtable_html(raw):
    match = KBTABLE_RE.search(raw)
    if match is None:
        return raw
    return match.group(0)


def weekday_name(text):
    for label, normalized in WEEKDAY_LABELS:
        if label in text:
            return normalized
    return ""


def is_empty_cell(text):
    cleaned = html.unescape(text).replace("\xa0", " ").strip()
    return cleaned == "" or cleaned == "-"


def visible_kbcontent(cell_html):
    parts = []
    for attrs, inner in DIV_RE.findall(cell_html):
        lower = attrs.lower().replace(" ", "")
        if "kbcontent" not in lower:
            continue
        if "display:none" in lower:
            continue
        text = strip_tags(inner)
        if not is_empty_cell(text):
            parts.append(text)
    return parts


def cell_lines(cell_html):
    parts = visible_kbcontent(cell_html)
    if not parts:
        text = strip_tags(cell_html)
        if is_empty_cell(text):
            return []
        parts = [text]
    lines = []
    for part in parts:
        cleaned = html.unescape(part).replace("\xa0", " ")
        for token in cleaned.split():
            if token and token != "-":
                lines.append(token)
    return lines


def header_days(header_row):
    days = []
    for cell in header_row:
        days.append(weekday_name(strip_tags(cell)))
    return days


def schedule_item(day, period, lines):
    return {
        "day": day,
        "period": period,
        "course_name": lines[0],
        "lines": lines,
        "text": " ".join(lines),
    }


def parse_schedule(raw):
    rows = parse_table_html(kbtable_html(raw))
    if not rows:
        return rows, []
    days = header_days(rows[0])
    items = []
    if not any(days):
        return rows, items
    for row in rows[1:]:
        period = strip_tags(row[0]) if row else ""
        if weekday_name(period):
            continue
        for index in range(1, len(row)):
            day = days[index] if index < len(days) else ""
            if day == "":
                continue
            lines = cell_lines(row[index])
            if not lines:
                continue
            if weekday_name(lines[0]) and len(lines) == 1:
                continue
            items.append(schedule_item(day, period, lines))
    return rows, items


def schedule_url(semester, week, mode):
    params = {"sfFD": "1"}
    if semester:
        params["xnxq01id"] = semester
    if week:
        params["zc"] = week
    if mode:
        params["kbjcmsid"] = mode
    return SCHEDULE_URL + "?" + urlencode(params)


def schedule(client, semester, week, mode):
    semester = (semester or "").strip()
    week = (week or "").strip()
    mode = (mode or "").strip()
    target = schedule_url(semester, week, mode)
    status, final_url, raw = client.text("GET", target)
    if status != 200 or contains_any(raw, LOGIN_MARKERS):
        raise JWXTError(
            "schedule page requires login",
            "run easy-qfnu jwxt status or login again",
        )
    rows, items = parse_schedule(raw)
    if not items:
        trace.note("schedule table: " + str(len(rows)) + " rows, 0 data cells")
    return success(
        "jwxt",
        {
            "semester": semester,
            "week": week,
            "kbjcmsid": mode,
            "count": len(items),
            "items": items,
            "schedule": items,
            "url": final_url,
            "session_path": client.session_path,
        },
    )
