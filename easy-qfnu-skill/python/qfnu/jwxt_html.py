"""教务 HTML 表格和标签属性。"""

import html
import re

from .jwxt_auth import strip_tags

ROW_RE = re.compile(r"(?is)<tr\b[^>]*>(.*?)</tr\s*>")
CELL_RE = re.compile(r"(?is)<(?:td|th)\b[^>]*>(.*?)</(?:td|th)\s*>")
LOGIN_MARKERS = ["请输入账号", "请输入密码", "请输入验证码"]


def parse_table_html(raw):
    rows = []
    for row in ROW_RE.findall(raw):
        cells = CELL_RE.findall(row)
        if cells:
            rows.append(cells)
    return rows


def parse_table(raw):
    rows = []
    for html_row in parse_table_html(raw):
        cells = []
        for cell in html_row:
            cells.append(strip_tags(cell))
        if cells:
            rows.append(cells)
    return rows


def attr(tag, name):
    quoted = re.compile(
        r"(?is)\b" + re.escape(name) + r'\s*=\s*"([^"]*)"|\b' + re.escape(name) + r"\s*=\s*'([^']*)'"
    )
    match = quoted.search(tag)
    if match:
        if match.group(1):
            return html.unescape(match.group(1))
        return html.unescape(match.group(2) or "")
    unquoted = re.compile(r"(?is)\b" + re.escape(name) + r"\s*=\s*([^\s>]+)")
    match = unquoted.search(tag)
    if match:
        return html.unescape(match.group(1).strip("\"'"))
    return ""
