"""只读选课查询。进入开放轮次后搜课程 JSON，不调用 *Oper 选课接口。"""

import json
import re
from urllib.parse import urlencode

from . import telemetry
from .jwxt_auth import contains_any, strip_tags
from .jwxt_client import (
    XK_ENTER_URL,
    XK_LIST_URL,
    XK_SEARCH_ROOT,
    JWXTClient,
    JWXTError,
)
from .jwxt_html import CELL_RE, LOGIN_MARKERS, ROW_RE
from .result import failure, success, write_json

XK_LIVE_NOTICE = (
    "这是选课轮次即时查询：数据来自当前开放轮次的教务库，比公开预选课缓存更准确及时。"
    "默认会扫描全部选课模块，因此能探测目标课程实际所在模块；网页前端可能按年级隐藏部分模块入口，本查询不受该限制。"
    "本命令只读，不会提交选课。"
)
XK_CACHED_HINT = "当前没有开放的选课轮次时，可改用 easy-qfnu precourse search 查询公开预选课缓存（定时快照，可能滞后）"
XK_PARSE_HINT = "支持 easy-qfnu jwxt xk rounds 与 easy-qfnu jwxt xk search"
ID_FROM_QUERY = re.compile(r"jx0502zbid=([A-Za-z0-9]+)")
ID_FROM_CALL = re.compile(r"(?i)(?:xsxkFun|jrxk)\(['\"]([A-Za-z0-9]+)['\"]\)")
XK_MODULES = (
    {"key": "knjxk", "path": "xsxkKnjxk", "come_in": "comeInKnjxk", "name": "专业内跨年级选课"},
    {"key": "bxqjhxk", "path": "xsxkBxqjhxk", "come_in": "comeInBxqjhxk", "name": "本学期计划选课"},
    {"key": "xxxk", "path": "xsxkXxxk", "come_in": "comeInXxxk", "name": "选修选课"},
    {"key": "fawxk", "path": "xsxkFawxk", "come_in": "comeInFawxk", "name": "计划外选课"},
    {"key": "ggxxkxk", "path": "xsxkGgxxkxk", "come_in": "comeInGgxxkxk", "name": "公选课选课"},
)
MODULE_ALIASES = {
    "knjxk": "knjxk",
    "xsxkknjxk": "knjxk",
    "跨年级": "knjxk",
    "专业内跨年级选课": "knjxk",
    "bxqjhxk": "bxqjhxk",
    "xsxkbxqjhxk": "bxqjhxk",
    "本学期计划": "bxqjhxk",
    "本学期计划选课": "bxqjhxk",
    "计划选课": "bxqjhxk",
    "xxxk": "xxxk",
    "xsxkxxxk": "xxxk",
    "选修": "xxxk",
    "选修选课": "xxxk",
    "fawxk": "fawxk",
    "xsxkfawxk": "fawxk",
    "计划外": "fawxk",
    "计划外选课": "fawxk",
    "ggxxkxk": "ggxxkxk",
    "xsxkggxxkxk": "ggxxkxk",
    "公选": "ggxxkxk",
    "公选课": "ggxxkxk",
    "公选课选课": "ggxxkxk",
}


def run_jwxt_xk(args, out):
    for arg in args:
        if arg == "--help" or arg == "-h":
            return usage_xk(out)
    if len(args) == 0:
        return usage_xk(out)
    action = args[0]
    try:
        query = parse_xk_command(action, args[1:])
    except ValueError as exc:
        return write_json(out, failure("jwxt", str(exc), XK_PARSE_HINT))
    try:
        client = JWXTClient()
        client.load()
    except OSError as exc:
        return write_json(out, failure("jwxt", str(exc), "请检查本地会话文件"))
    try:
        result = execute_xk(client, action, query)
    except JWXTError as exc:
        telemetry.report_usage("jwxt.xk." + action, "failure")
        return write_json(out, failure("jwxt", exc.message, exc.hint))
    except OSError as exc:
        telemetry.report_usage("jwxt.xk." + action, "failure")
        return write_json(out, failure("jwxt", str(exc), "请检查网络和本地会话后重试"))
    telemetry.report_usage("jwxt.xk." + action, "success")
    return write_json(out, result)


def usage_xk(out):
    text = (
        "Usage: easy-qfnu jwxt xk <rounds|search>\n"
        "  easy-qfnu jwxt xk rounds\n"
        "  easy-qfnu jwxt xk search [--round ID] [--module 公选课] [--course 课程] [--teacher 教师] [--limit 50]\n"
        "  search 默认扫描全部选课模块，结果里的 located_modules 表示目标课程实际所在模块。\n"
    )
    try:
        out.write(text)
    except OSError:
        return 1
    return 2


def parse_xk_command(action, args):
    if action != "rounds" and action != "search":
        raise ValueError("unknown xk action: " + action)
    if action == "rounds":
        if len(args) > 0:
            raise ValueError("rounds 不接受额外参数")
        return {}
    query = {"round_id": "", "course": "", "teacher": "", "limit": 50, "modules": []}
    module_keys = []
    index = 0
    while index < len(args):
        if args[index] == "--help":
            raise ValueError("search 用法见 easy-qfnu jwxt xk --help")
        if index + 1 >= len(args):
            raise ValueError(args[index] + " requires a value")
        set_xk_option(query, module_keys, args[index], args[index + 1].strip())
        index += 2
    if query["limit"] < 1 or query["limit"] > 500:
        raise ValueError("--limit 必须是 1 到 500")
    query["modules"] = resolve_xk_modules(module_keys)
    return query


def set_xk_option(query, module_keys, arg, value):
    if arg == "--round":
        query["round_id"] = value
    elif arg == "--module":
        module_keys.append(value)
    elif arg == "--course":
        query["course"] = value
    elif arg == "--teacher":
        query["teacher"] = value
    elif arg == "--limit":
        try:
            query["limit"] = int(value)
        except ValueError:
            raise ValueError("--limit must be an integer") from None
    else:
        raise ValueError("unknown option: " + arg)


def find_xk_module(raw):
    key = MODULE_ALIASES.get(raw.strip().lower())
    if key is None:
        key = MODULE_ALIASES.get(raw.strip())
    if key is None:
        return None
    for module in XK_MODULES:
        if module["key"] == key:
            return module
    return None


def resolve_xk_modules(keys):
    if not keys:
        return list(XK_MODULES)
    result = []
    seen = set()
    for key in keys:
        module = find_xk_module(key)
        if module is None:
            raise ValueError("unknown module: " + key)
        if module["key"] in seen:
            continue
        seen.add(module["key"])
        result.append(module)
    return result


def execute_xk(client, action, query):
    if action == "rounds":
        return xk_rounds(client)
    return xk_search(client, query)


def round_payload(item):
    payload = {"id": item["id"]}
    if item.get("name"):
        payload["name"] = item["name"]
    if item.get("start"):
        payload["start"] = item["start"]
    if item.get("end"):
        payload["end"] = item["end"]
    return payload


def xk_rounds(client):
    rounds = fetch_xk_rounds(client)
    items = []
    for item in rounds:
        items.append(round_payload(item))
    return success(
        "jwxt",
        {
            "operation": "xk.rounds",
            "query_kind": "live",
            "notice": XK_LIVE_NOTICE,
            "count": len(items),
            "rounds": items,
        },
    )


def xk_search(client, query):
    rounds = fetch_xk_rounds(client)
    selected = pick_xk_round(rounds, query["round_id"])
    enter_xk_round(client, selected["id"])
    items = []
    for module in query["modules"]:
        items.extend(search_xk_module(client, module, query["course"], query["teacher"]))
    located = summarize_xk_modules(items)
    truncated = len(items) > query["limit"]
    if truncated:
        items = items[: query["limit"]]
    return success(
        "jwxt",
        {
            "operation": "xk.search",
            "query_kind": "live",
            "notice": XK_LIVE_NOTICE,
            "round": round_payload(selected),
            "scanned_modules": xk_module_keys(query["modules"]),
            "located_modules": located,
            "count": len(items),
            "limit": query["limit"],
            "truncated": truncated,
            "items": items,
        },
    )


def xk_session_error(status, raw, allow_redirect):
    if contains_any(raw, ["您的账号在其它地方登录"]):
        raise JWXTError("教务账号已在其它地方登录", "停止自动重试；确认后再运行 easy-qfnu jwxt login")
    ok = status == 200 or (allow_redirect and (status == 302 or status == 301))
    if not ok:
        raise JWXTError("选课页面返回 HTTP " + str(status), "请检查网络后重试")
    if contains_any(raw, LOGIN_MARKERS):
        raise JWXTError("选课查询需要已登录的教务会话", "先运行 easy-qfnu jwxt login 或 jwxt status")


def fetch_xk_rounds(client):
    status, _final, raw = client.text("GET", XK_LIST_URL)
    xk_session_error(status, raw, False)
    rounds = parse_xk_rounds(raw)
    if not rounds:
        raise JWXTError("当前没有开放的选课轮次", XK_CACHED_HINT)
    return rounds


def enter_xk_round(client, round_id):
    target = XK_ENTER_URL + "?" + urlencode({"jx0502zbid": round_id})
    status, _final, raw = client.text("GET", target)
    if status >= 400:
        raise JWXTError("进入选课轮次返回 HTTP " + str(status), "请确认轮次仍开放后重试")
    xk_session_error(status, raw, True)


def search_xk_module(client, module, course, teacher):
    params = {"kcxx": course, "skls": teacher, "sfym": "false", "sfct": "false", "sfxx": "false"}
    target = XK_SEARCH_ROOT + "/" + module["path"] + "?" + urlencode(params)
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": XK_SEARCH_ROOT + "/" + module["come_in"],
    }
    body = urlencode({"iDisplayStart": "0", "iDisplayLength": "10000"}).encode("utf-8")
    status, _final, raw = client.text("POST", target, body, headers)
    xk_session_error(status, raw, False)
    return parse_xk_courses(raw, module)


def first_xk_round_id(raw):
    match = ID_FROM_QUERY.search(raw)
    if match is not None:
        return match.group(1)
    match = ID_FROM_CALL.search(raw)
    if match is not None:
        return match.group(1)
    return ""


def add_xk_round(order, seen, round_id):
    if round_id == "":
        return None
    if round_id in seen:
        return seen[round_id]
    item = {"id": round_id, "name": "", "start": "", "end": ""}
    seen[round_id] = item
    order.append(round_id)
    return item


def fill_xk_round_row(item, cells):
    if not cells or "选课轮次名称" in cells[0]:
        return
    if item["name"] == "":
        item["name"] = cells[0]
    if item["start"] == "" and len(cells) > 1:
        item["start"] = cells[1]
    if item["end"] == "" and len(cells) > 2:
        item["end"] = cells[2]


def parse_xk_rounds(raw):
    order = []
    seen = {}
    for match in ID_FROM_QUERY.findall(raw):
        add_xk_round(order, seen, match)
    for match in ID_FROM_CALL.findall(raw):
        add_xk_round(order, seen, match)
    for inner in ROW_RE.findall(raw):
        round_id = first_xk_round_id(inner)
        item = add_xk_round(order, seen, round_id)
        if item is None:
            continue
        cells = []
        for cell in CELL_RE.findall(inner):
            cells.append(strip_tags(cell))
        fill_xk_round_row(item, cells)
    rounds = []
    for round_id in order:
        rounds.append(seen[round_id])
    return rounds


def xk_string(value):
    if value is None:
        return ""
    text = str(value).strip()
    if text == "" or text == "None":
        return ""
    return strip_tags(text).strip()


def xk_course_item(row, module):
    return {
        "module": module["key"],
        "module_name": module["name"],
        "course_code": xk_string(row.get("kch")),
        "course_name": xk_string(row.get("kcmc")),
        "teacher": xk_string(row.get("skls")),
        "remaining": xk_string(row.get("syrs")),
        "selected": row.get("xkrs"),
        "capacity": row.get("pkrs"),
        "schedule": xk_string(row.get("sksj")),
        "location": xk_string(row.get("skdd")),
        "college": xk_string(row.get("dwmc")),
        "class_name": xk_string(row.get("ktmc")),
        "conflict": xk_string(row.get("ctsm")),
    }


def parse_xk_courses(raw, module):
    try:
        parsed = json.loads(raw)
    except ValueError:
        raise JWXTError("选课查询未返回课程 JSON", "确认已进入开放轮次；网页前端隐藏模块时仍可查询") from None
    if not isinstance(parsed, dict):
        raise JWXTError("选课查询未返回课程 JSON", "确认已进入开放轮次；网页前端隐藏模块时仍可查询")
    rows = parsed.get("aaData")
    if rows is None:
        rows = []
    elif not isinstance(rows, list):
        raise JWXTError("选课查询未返回课程 JSON", "确认已进入开放轮次；网页前端隐藏模块时仍可查询")
    items = []
    for row in rows:
        if isinstance(row, dict):
            items.append(xk_course_item(row, module))
    return items


def summarize_xk_modules(items):
    counts = {}
    names = {}
    order = []
    for item in items:
        key = item.get("module") or ""
        name = item.get("module_name") or ""
        if key == "":
            continue
        if key not in counts:
            order.append(key)
            names[key] = name
            counts[key] = 0
        counts[key] += 1
    result = []
    for key in order:
        result.append({"key": key, "name": names[key], "count": counts[key]})
    return result


def xk_module_keys(modules):
    keys = []
    for module in modules:
        keys.append(module["key"])
    return keys


def pick_xk_round(rounds, round_id):
    round_id = (round_id or "").strip()
    if round_id != "":
        for item in rounds:
            if item["id"] == round_id:
                return item
        raise JWXTError("未找到指定选课轮次", "先运行 easy-qfnu jwxt xk rounds 查看开放轮次")
    if len(rounds) == 1:
        return rounds[0]
    raise JWXTError("当前有多个开放选课轮次，需要指定 --round", "先运行 easy-qfnu jwxt xk rounds，再带上 --round <id>")
