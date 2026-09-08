"""只读课程成绩。GET /jsxsd/kscj/cjcx_list，不提交表单。"""

from urllib.parse import urlencode

from . import trace
from .jwxt_auth import contains_any
from .jwxt_client import GRADE_URL, JWXTError
from .jwxt_html import LOGIN_MARKERS, parse_table
from .result import success

GRADE_HEADERS = {
    "开课学期": "semester",
    "课程编号": "course_id",
    "课程名称": "course_name",
    "分组名": "group_name",
    "成绩": "score",
    "成绩标识": "score_flag",
    "学分": "credits",
    "总学时": "total_hours",
    "绩点": "gpa",
    "补重学期": "makeup_semester",
    "考核方式": "assessment_method",
    "考试性质": "exam_nature",
    "课程属性": "course_attribute",
    "课程性质": "course_nature",
    "课程类别": "course_category",
}


def grade_item(headers, row, semester):
    item = {}
    for index, header in enumerate(headers):
        if index >= len(row):
            continue
        key = GRADE_HEADERS.get(header.strip())
        if key:
            item[key] = row[index]
    if not item:
        return None
    if semester:
        item["semester"] = semester
    return item


def parse_grades(raw, semester):
    rows = parse_table(raw)
    if len(rows) < 2:
        return rows, []
    items = []
    for row in rows[1:]:
        item = grade_item(rows[0], row, semester)
        if item is not None:
            items.append(item)
    return rows, items


def grades(client, semester):
    semester = (semester or "").strip()
    target = GRADE_URL
    if semester:
        target = GRADE_URL + "?" + urlencode({"kksj": semester})
    status, final_url, raw = client.text("GET", target)
    if status != 200 or contains_any(raw, LOGIN_MARKERS):
        raise JWXTError(
            "grades page requires login",
            "run easy-qfnu jwxt status or login again",
        )
    rows, items = parse_grades(raw, semester)
    if not items:
        trace.note("grade table: " + str(len(rows)) + " rows, 0 mapped courses")
    return success(
        "jwxt",
        {
            "semester": semester,
            "count": len(items),
            "items": items,
            "grades": items,
            "url": final_url,
            "session_path": client.session_path,
        },
    )
