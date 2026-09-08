"""学生评价：列出未评课程；evaluate 默认预览，--confirm 才提交。"""

import re
from urllib.parse import urlencode, urljoin, urlparse

from .jwxt_auth import contains_any, strip_tags
from .jwxt_client import EVALUATION_FIND_URL, EVALUATION_SAVE_URL, JWXT_BASE, JWXTError
from .jwxt_html import LOGIN_MARKERS, ROW_RE, attr, parse_table_html
from .result import success

LINK_RE = re.compile(r"(?is)<a\b([^>]*)>(.*?)</a\s*>")
DATALIST_RE = re.compile(r'(?is)<table\b[^>]*id=["\']dataList["\'][^>]*>(.*?)</table\s*>')
ANCHOR_RE = re.compile(r"(?is)<a\b([^>]*)>")
FORM_RE = re.compile(r'(?is)<form\b[^>]*id=["\']Form1["\'][^>]*>(.*?)</form\s*>')
INPUT_RE = re.compile(r"(?is)<input\b[^>]*>")
RADIO_RE = re.compile(r'(?is)<input\b[^>]*type=["\']radio["\'][^>]*>')
SCORE_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)")
SUCCESS_MARKERS = ["保存成功", "提交成功", "评价成功"]
PREVIEW_HINT = "当前为预览模式；确认课程、教师和分数后，重新运行相同命令并追加 --confirm 才会提交"
SCORE_100_WARNING = "目标 100 分可能触发教务系统的选项限制，建议使用 98 或更低分数"
STOP_HINT = "上一门课程提交结果异常，已停止后续提交；请先核对官方页面"


def datalist_html(raw):
    match = DATALIST_RE.search(raw)
    if match is None:
        return raw
    return match.group(0)


def form1_html(raw):
    match = FORM_RE.search(raw)
    if match is None:
        return raw
    return match.group(1)


def evaluation_list_url(raw):
    for attrs, inner in LINK_RE.findall(raw):
        href = attr(attrs, "href")
        if href == "":
            continue
        if "/jsxsd/xspj/xspj_list.do" in href or "进入评价" in strip_tags(inner):
            return urljoin(EVALUATION_FIND_URL, href)
    return ""


def evaluation_headers(cells):
    headers = []
    for cell in cells:
        headers.append(strip_tags(cell))
    return headers


def apply_evaluation_cell(item, header, value, raw):
    if header == "课程名称" or header == "课程":
        item["course_name"] = value
    elif header == "授课教师" or header == "教师":
        item["teacher_name"] = value
    elif header == "是否提交" or header == "提交状态":
        if "是" in value or "已" in value:
            item["status"] = "已提交"
    elif header == "已评" or header == "评价状态":
        if "是" in value or "已" in value:
            item["status"] = "已评"
    elif header == "操作":
        link = ANCHOR_RE.search(raw)
        if link is not None:
            item["href"] = urljoin(EVALUATION_FIND_URL, attr(link.group(1), "href"))


def evaluation_row(index, cells, headers):
    item = {
        "id": str(index),
        "course_name": "未知课程",
        "teacher_name": "未知教师",
        "status": "未评",
    }
    for position, header in enumerate(headers):
        if position >= len(cells):
            continue
        apply_evaluation_cell(item, header.strip(), strip_tags(cells[position]), cells[position])
    return item


def evaluation_rows(raw):
    rows = parse_table_html(datalist_html(raw))
    if len(rows) < 2:
        return []
    headers = evaluation_headers(rows[0])
    items = []
    for index, row in enumerate(rows[1:]):
        items.append(evaluation_row(index, row, headers))
    return items


def evaluations(client):
    status, _final, raw = client.text("GET", EVALUATION_FIND_URL)
    if status != 200 or contains_any(raw, LOGIN_MARKERS):
        raise JWXTError(
            "evaluation page requires login",
            "run easy-qfnu jwxt status or login again",
        )
    list_url = evaluation_list_url(raw)
    if list_url == "":
        raise JWXTError("no active evaluation batch", "当前没有可用的评教批次")
    list_status, final_url, list_raw = client.text("GET", list_url)
    if list_status != 200:
        raise JWXTError("evaluation list page unavailable", "请检查登录会话后重试")
    items = evaluation_rows(list_raw)
    return success(
        "jwxt",
        {
            "batch_url": list_url,
            "page_count": 1,
            "count": len(items),
            "items": items,
            "evaluations": items,
            "url": final_url,
            "session_path": client.session_path,
        },
    )


def evaluation_static_fields(form):
    fields = {}
    for tag in INPUT_RE.findall(form):
        name = attr(tag, "name")
        typ = attr(tag, "type").lower()
        if name != "" and typ == "hidden" and name != "pj06xh":
            fields[name] = attr(tag, "value")
    return fields


def evaluation_score(row):
    match = SCORE_RE.search(row)
    if match is None:
        return 0.0
    try:
        return float(match.group(1))
    except ValueError:
        raise JWXTError("invalid evaluation option score " + match.group(1)) from None


def evaluation_options(row):
    radios = RADIO_RE.findall(row)
    if not radios:
        return []
    score = evaluation_score(row)
    options = []
    for tag in radios:
        option_id = attr(tag, "value")
        if option_id != "":
            options.append({"option_id": option_id, "label": option_id, "score": score})
    return options


def parse_evaluation_indicator(row):
    indicator = ""
    for tag in INPUT_RE.findall(row):
        if attr(tag, "name") == "pj06xh":
            indicator = attr(tag, "value")
            break
    if indicator == "":
        return "", []
    return indicator, evaluation_options(row)


def parse_evaluation_detail(raw, summary):
    form = form1_html(raw)
    detail = {
        "summary": summary,
        "static": evaluation_static_fields(form),
        "ids": [],
        "options": {},
    }
    for row in ROW_RE.findall(form):
        indicator, options = parse_evaluation_indicator(row)
        if indicator == "" or not options:
            continue
        detail["ids"].append(indicator)
        detail["options"][indicator] = options
    if not detail["ids"]:
        raise JWXTError("evaluation indicators not found", "当前课程的评教指标无法解析")
    return detail


def evaluation_detail(client, summary):
    href = summary.get("href")
    if not isinstance(href, str) or href.strip() == "":
        raise JWXTError("evaluation detail link not found", "课程列表没有提供评教链接")
    href = href.strip()
    parsed = urlparse(href)
    if parsed.netloc != "zhjw.qfnu.edu.cn":
        raise JWXTError("evaluation detail URL is outside JWXT host")
    status, _final, raw = client.text("GET", href)
    if status != 200:
        raise JWXTError("evaluation detail page unavailable", "请检查登录会话后重试")
    if contains_any(raw, LOGIN_MARKERS):
        raise JWXTError("evaluation detail requires login", "请先重新登录教务系统")
    return parse_evaluation_detail(raw, summary)


def evaluation_option_values(option):
    option_id = option.get("option_id")
    if not isinstance(option_id, str) or option_id.strip() == "":
        raise JWXTError("evaluation option is missing option_id")
    score = option.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise JWXTError("evaluation option " + option_id + " has an invalid score")
    return option_id, float(score)


def add_preset_option(nxt, total, selections, indicator_id, option):
    value, score = evaluation_option_values(option)
    candidate = dict(selections)
    candidate[indicator_id] = value
    combined = total + int(score * 100 + 0.5)
    if combined not in nxt:
        nxt[combined] = candidate


def evaluation_preset(detail, target):
    totals = {0: {}}
    for indicator_id in detail["ids"]:
        nxt = {}
        for total, selections in totals.items():
            for option in detail["options"].get(indicator_id) or []:
                add_preset_option(nxt, total, selections, indicator_id, option)
        if not nxt:
            raise JWXTError("evaluation indicator " + indicator_id + " has no valid options")
        totals = nxt
    best, best_distance = 0, 10**18
    for total in totals:
        distance = total - target * 100
        if distance < 0:
            distance = -distance
        if distance < best_distance or (distance == best_distance and total > best):
            best = total
            best_distance = distance
    return totals[best], best / 100.0


def evaluation_preview(detail, selections):
    preview = []
    for indicator_id in detail["ids"]:
        found = False
        for option in detail["options"][indicator_id]:
            option_id, score = evaluation_option_values(option)
            if option_id != selections.get(indicator_id):
                continue
            label = option.get("label")
            if not isinstance(label, str) or label.strip() == "":
                raise JWXTError("evaluation option " + option_id + " is missing label")
            preview.append({"id": indicator_id, "option": label, "score": score})
            found = True
            break
        if not found:
            raise JWXTError("evaluation indicator " + indicator_id + " has no selected option")
    return preview


def format_score(score):
    return format(score, "f").rstrip("0").rstrip(".")


def encode_evaluation_form(detail, selections):
    pairs = []
    for key, value in detail["static"].items():
        pairs.append((key, value))
    pairs.append(("issubmit", "1"))
    for indicator_id in detail["ids"]:
        selected = selections.get(indicator_id) or ""
        if selected == "":
            raise JWXTError("evaluation indicator " + indicator_id + " has no selection")
        pairs.append(("pj06xh", indicator_id))
        pairs.append(("pj0601id_" + indicator_id, selected))
        for option in detail["options"][indicator_id]:
            option_id, score = evaluation_option_values(option)
            pairs.append(("pj0601fz_" + indicator_id + "_" + option_id, format_score(score)))
    return urlencode(pairs).encode("utf-8")


def submit_evaluation(client, detail, selections):
    body = encode_evaluation_form(detail, selections)
    href = str(detail["summary"].get("href") or "")
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": href,
        "Origin": JWXT_BASE,
    }
    status, _final, raw = client.text("POST", EVALUATION_SAVE_URL, body, headers)
    if status != 200:
        raise JWXTError(
            "evaluation submit HTTP " + str(status),
            "提交状态不确定，请登录教务系统官方页面核对；不会自动重复提交",
        )
    message = strip_tags(raw)
    if contains_any(message, SUCCESS_MARKERS):
        return message
    raise JWXTError(
        "evaluation submit result was not confirmed",
        "教务系统未返回成功提示，请登录官方页面核对状态",
    )


def contains_string(values, target):
    for value in values:
        if value.strip() == target:
            return True
    return False


def error_text(exc):
    if isinstance(exc, JWXTError):
        return exc.message
    return str(exc)


def build_evaluation_plan(client, item, score):
    detail = evaluation_detail(client, item)
    selections, total = evaluation_preset(detail, score)
    indicators = evaluation_preview(detail, selections)
    preview = {
        "id": item["id"],
        "course_name": item["course_name"],
        "teacher_name": item["teacher_name"],
        "target_score": score,
        "total_score": total,
        "indicators": indicators,
    }
    return preview, {"detail": detail, "selections": selections}


def build_evaluation_plans(client, score, courses):
    listing = evaluations(client)
    items = listing.get("items")
    if not isinstance(items, list):
        raise JWXTError("evaluation listing has invalid items")
    preview = []
    plans = []
    for item in items:
        if courses and not contains_string(courses, str(item.get("id"))):
            continue
        status = item.get("status")
        if not isinstance(status, str):
            raise JWXTError("evaluation item has invalid status")
        if status != "未评":
            continue
        preview_item, plan = build_evaluation_plan(client, item, score)
        preview.append(preview_item)
        plans.append(plan)
    return preview, plans


def evaluation_result(client, score, preview):
    result = success(
        "jwxt",
        {
            "action": "evaluate",
            "target_score": score,
            "count": len(preview),
            "items": preview,
            "evaluation_preview": preview,
            "session_path": client.session_path,
        },
    )
    if score == 100:
        result["warning"] = SCORE_100_WARNING
    return result


def failed_evaluation_result(result, preview, results, entry, index, submit_err):
    entry["ok"] = False
    entry["error"] = error_text(submit_err)
    results.append(entry)
    for skipped in preview[index + 1 :]:
        results.append(
            {
                "id": skipped["id"],
                "course_name": skipped["course_name"],
                "teacher_name": skipped["teacher_name"],
                "ok": False,
                "skipped": True,
                "hint": STOP_HINT,
            }
        )
    result["ok"] = False
    result["dry_run"] = False
    result["submitted"] = 0
    result["failed"] = 1
    result["skipped"] = len(results) - 1
    result["results"] = results
    result["hint"] = "部分课程提交失败；请根据 error 登录教务系统核对状态"
    return result


def submit_evaluation_plans(client, result, preview, plans):
    results = []
    for index, plan in enumerate(plans):
        item = preview[index]
        entry = {
            "id": item["id"],
            "course_name": item["course_name"],
            "teacher_name": item["teacher_name"],
            "total_score": item["total_score"],
        }
        try:
            message = submit_evaluation(client, plan["detail"], plan["selections"])
        except (JWXTError, OSError) as exc:
            return failed_evaluation_result(result, preview, results, entry, index, exc)
        entry["ok"] = True
        entry["message"] = message
        results.append(entry)
    result["dry_run"] = False
    result["submitted"] = len(results)
    result["failed"] = 0
    result["skipped"] = 0
    result["results"] = results
    return result


def evaluate(client, score, courses, confirm):
    if score < 0 or score > 100:
        raise JWXTError("target score must be between 0 and 100")
    preview, plans = build_evaluation_plans(client, score, courses)
    result = evaluation_result(client, score, preview)
    if not confirm:
        result["dry_run"] = True
        result["requires_confirmation"] = len(preview) > 0
        result["hint"] = PREVIEW_HINT
        return result
    return submit_evaluation_plans(client, result, preview, plans)
