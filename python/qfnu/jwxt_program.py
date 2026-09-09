"""只读培养方案及完成情况。解析对齐 easy-qfnu-web 的 table#mxh。"""

import re
from urllib.parse import urljoin, urlparse

from . import trace
from .jwxt_auth import contains_any, strip_tags
from .jwxt_client import JWXT_BASE, PROGRAM_URL, JWXTError
from .jwxt_html import LOGIN_MARKERS, frame_sources, parse_table
from .result import success

MXH_RE = re.compile(r"(?is)<table\b[^>]*\bid\s*=\s*['\"]mxh['\"][^>]*>.*?</table\s*>")
PYMB_RE = re.compile(r"(?is)<span\b[^>]*\bid\s*=\s*['\"]pymb['\"][^>]*>(.*?)</span\s*>")
CAPTION_RE = re.compile(r"(?is)<caption\b[^>]*>(.*?)</caption\s*>")
GROUP_HEADER_RE = re.compile(
    r"(.*?)\s*[（(]应修\s*([\d.]+)\s*/\s*已修\s*([\d.]+)[）)]"
)
SKIP_ROW_MARKERS = ("小计", "合计", "学年学期", "课程体系", "讲课学时")


def parse_program(raw):
    meta = {
        "program_name": caption_text(raw),
        "objectives": "",
        "description": "",
    }
    spans = [strip_tags(inner) for inner in PYMB_RE.findall(raw)]
    if spans:
        meta["objectives"] = spans[0]
    if len(spans) >= 2:
        meta["description"] = spans[1]
    groups = parse_course_groups(raw)
    items = flatten_courses(groups)
    return meta, groups, items


def caption_text(raw):
    match = CAPTION_RE.search(raw)
    if not match:
        return ""
    return strip_tags(match.group(1))


def parse_course_groups(raw):
    match = MXH_RE.search(raw)
    if not match:
        return []
    groups = []
    current = None
    for row in parse_table(match.group(0)):
        joined = "".join(row)
        if any(marker in joined for marker in SKIP_ROW_MARKERS):
            continue
        first = (row[0] if row else "").replace("\xa0", " ").strip()
        if "应修" in first and "已修" in first:
            if current is not None:
                groups.append(current)
            name, required, earned = parse_group_header(first)
            current = {
                "group_name": name,
                "required_credits": required,
                "earned_credits": earned,
                "courses": [],
            }
            course = course_from_row(row, 2)
        else:
            course = course_from_row(row, 1)
        if current is not None and course is not None:
            current["courses"].append(course)
    if current is not None:
        groups.append(current)
    return groups


def parse_group_header(text):
    match = GROUP_HEADER_RE.search(text.replace("\xa0", " "))
    if match:
        return match.group(1).strip(), match.group(2), match.group(3)
    return text.strip(), "", ""


def course_from_row(row, offset):
    if len(row) <= offset + 5:
        return None
    course_name = cell(row, offset + 1)
    if not course_name:
        return None
    return {
        "course_code": cell(row, offset),
        "course_name": course_name,
        "status": cell(row, offset + 2),
        "course_prop": cell(row, offset + 3),
        "course_attr": cell(row, offset + 4),
        "credits": cell(row, offset + 5),
        "hours": cell(row, -2),
        "term": cell(row, -1),
    }


def cell(row, index):
    if index < 0:
        index = len(row) + index
    if index < 0 or index >= len(row):
        return ""
    return (row[index] or "").replace("\xa0", " ").strip()


def flatten_courses(groups):
    items = []
    for group in groups:
        for course in group["courses"]:
            item = dict(course)
            item["group_name"] = group["group_name"]
            items.append(item)
    return items


def keyword_matches(item, keyword):
    blob = " ".join(str(value) for value in item.values())
    return keyword in blob


def filter_groups(groups, keyword):
    filtered = []
    for group in groups:
        if keyword in group["group_name"]:
            filtered.append(group)
            continue
        courses = [course for course in group["courses"] if keyword_matches(course, keyword)]
        if courses:
            copied = dict(group)
            copied["courses"] = courses
            filtered.append(copied)
    return filtered


def same_origin(url):
    parsed = urlparse(url)
    origin = urlparse(JWXT_BASE)
    return parsed.scheme.lower() == origin.scheme.lower() and parsed.netloc.lower() == origin.netloc.lower()


def follow_frame(client, page_url, raw):
    for src in frame_sources(raw):
        if src.lower().startswith("javascript:") or src.lower().startswith("about:"):
            continue
        target = urljoin(page_url, src)
        if not same_origin(target):
            continue
        return fetch_program_page(client, target)
    return page_url, raw


def fetch_program_page(client, url):
    status, final_url, raw = client.text("GET", url)
    if status != 200 or contains_any(raw, LOGIN_MARKERS):
        raise JWXTError(
            "program page requires login",
            "run easy-qfnu jwxt status or login again",
        )
    return final_url, raw


def program(client, keyword=""):
    keyword = (keyword or "").strip()
    final_url, raw = fetch_program_page(client, PROGRAM_URL)
    meta, groups, items = parse_program(raw)
    if not groups:
        final_url, raw = follow_frame(client, final_url, raw)
        meta, groups, items = parse_program(raw)
    if keyword:
        groups = filter_groups(groups, keyword)
        items = flatten_courses(groups)
    if not groups:
        trace.note("program tables: no table#mxh course groups")
    payload = {
        "count": len(items),
        "items": items,
        "program": items,
        "groups": groups,
        "url": final_url,
        "session_path": client.session_path,
    }
    payload.update(meta)
    if keyword:
        payload["keyword"] = keyword
    return success("jwxt", payload)
