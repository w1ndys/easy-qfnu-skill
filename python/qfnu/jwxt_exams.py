"""只读考试安排。GET /jsxsd/xsks/xsksap_query 取默认学期，POST /jsxsd/xsks/xsksap_list 取列表；不提交任何申请。"""

import re
from urllib.parse import urlencode

from . import trace
from .jwxt_auth import contains_any
from .jwxt_client import EXAM_LIST_URL, EXAM_QUERY_URL, JWXTError
from .jwxt_html import LOGIN_MARKERS, parse_table
from .result import success

DATALIST_RE = re.compile(
    r'(?is)<table\b[^>]*\bid\s*=\s*["\']dataList["\'][^>]*>(.*?)</table\s*>'
)
SEMESTER_SELECT_RE = re.compile(
    r'(?is)<select\b[^>]*\bid\s*=\s*["\']xnxqid["\'][^>]*>(.*?)</select\s*>'
)
OPTION_RE = re.compile(r"(?is)<option\b([^>]*)>")
OPTION_VALUE_RE = re.compile(r'(?is)\bvalue\s*=\s*["\']([^"\']*)["\']')

# key 是考试安排表头原文；表头改名时这里要同步，别的列一律忽略（含「操作」）。
EXAM_HEADERS = {
    "校区": "campus",
    "考试场次": "session",
    "课程编号": "course_id",
    "课程名称": "course_name",
    "授课教师": "teacher",
    "考试时间": "exam_time",
    "考场": "room",
    "座位号": "seat_no",
    "准考证号": "admission_no",
    "备注": "remark",
}
TERM_CATEGORY_CODES = {"1": "期初", "2": "期中", "3": "期末"}
TERM_CATEGORY_VALUES = {"期初": "1", "期中": "2", "期末": "3"}
NO_DATA_MARKERS = ("未查询到数据", "暂无数据", "无数据", "没有查询到")
NO_DATA_MESSAGE = "未查到数据"
CONTENT_FIELDS = ("course_id", "course_name", "exam_time", "room")


def datalist_html(raw):
    """只取 dataList 表格，避免把页面里其它表格当成考试行。"""
    match = DATALIST_RE.search(raw)
    if match is None:
        return raw
    return match.group(0)


def selected_semester(raw):
    """取查询页默认选中的学年学期；没有选中项时退回第一个非空选项。"""
    match = SEMESTER_SELECT_RE.search(raw)
    if match is None:
        return ""
    first = ""
    for option in OPTION_RE.finditer(match.group(1)):
        attrs = option.group(1)
        value = OPTION_VALUE_RE.search(attrs)
        if value is None:
            continue
        text = value.group(1).strip()
        if text == "":
            continue
        if first == "":
            first = text
        if "selected" in attrs.lower():
            return text
    return first


def term_category(value):
    """学期类别接受 1/2/3，也接受 期初/期中/期末。返回 (代码, 名称)。"""
    text = (value or "").strip()
    if text in TERM_CATEGORY_CODES:
        return text, TERM_CATEGORY_CODES[text]
    if text in TERM_CATEGORY_VALUES:
        return TERM_CATEGORY_VALUES[text], text
    return text, ""


def header_row(rows):
    """返回 (表头下标, 表头列)；找不到返回 (-1, [])。"""
    for index, row in enumerate(rows):
        if contains_any(" ".join(row), ("课程名称",)):
            return index, row
    return -1, []


def notice_text(row):
    """页面用单行 colspan 提示（例如 未查询到数据）时返回该文本。"""
    if len(row) != 1:
        return ""
    return row[0].strip()


def exam_item(headers, row):
    item = {}
    for index, header in enumerate(headers):
        if index >= len(row):
            continue
        key = EXAM_HEADERS.get(header.strip())
        if key is None:
            continue
        value = row[index].strip()
        if value:
            item[key] = value
    return item


def has_content(item):
    for key in CONTENT_FIELDS:
        if item.get(key):
            return True
    return False


def parse_exams(raw):
    """返回 (rows, items, notice)。notice 是页面自己给出的空数据提示。"""
    rows = parse_table(datalist_html(raw))
    if not rows:
        return rows, [], ""
    index, headers = header_row(rows)
    if index < 0:
        return rows, [], ""
    items = []
    notice = ""
    for row in rows[index + 1 :]:
        text = notice_text(row)
        if text != "":
            if notice == "" and contains_any(text, NO_DATA_MARKERS):
                notice = text
            continue
        item = exam_item(headers, row)
        if not has_content(item):
            continue
        items.append(item)
    return rows, items, notice


def fetch_exams(client, semester, code, label, referer):
    """提交查询表单取列表。表单字段与网页保持一致，不写任何业务表单。"""
    form = {
        "xqlbmc": label,
        "sxxnxq": "",
        "dqxnxq": "",
        "ckbz": "",
        "xnxqid": semester,
        "xqlb": code,
    }
    body = urlencode(form).encode("utf-8")
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": referer,
    }
    status, final_url, raw = client.text("POST", EXAM_LIST_URL, body, headers)
    if status != 200 or contains_any(raw, LOGIN_MARKERS):
        raise JWXTError(
            "exam list requires login",
            "run easy-qfnu jwxt status or login again",
        )
    rows, items, notice = parse_exams(raw)
    index, _headers = header_row(rows)
    if not items and notice == "" and index < 0:
        # 既不是 dataList 表也不是登录页：多半是页面结构变了，不能冒充「未查到数据」。
        raise JWXTError(
            "exam list page is not recognised",
            "rerun with --debug and inspect upstream.body; the exam page structure may have changed",
        )
    return final_url, rows, items, notice


def exams(client, semester, category):
    semester = (semester or "").strip()
    code, label = term_category(category)
    status, _, raw = client.text("GET", EXAM_QUERY_URL)
    if status != 200 or contains_any(raw, LOGIN_MARKERS):
        raise JWXTError(
            "exam query page requires login",
            "run easy-qfnu jwxt status or login again",
        )
    if semester == "":
        semester = selected_semester(raw)
    final_url, rows, items, notice = fetch_exams(client, semester, code, label, EXAM_QUERY_URL)
    fields = {
        "semester": semester,
        "term_category": code,
        "term_category_name": label,
        "count": len(items),
        "items": items,
        "exams": items,
        "url": final_url,
        "session_path": client.session_path,
    }
    if not items:
        # 页面显示「未查询到数据」时，直接给 agents 一个可原样转述的提示。
        fields["message"] = NO_DATA_MESSAGE
        trace.note("exam list: " + str(len(rows)) + " rows, notice=" + (notice or "<none>"))
    return success("jwxt", fields)
