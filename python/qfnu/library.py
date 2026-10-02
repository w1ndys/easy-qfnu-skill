"""图书馆预约系统只读查询：区域列表与座位状态。

区域/楼层/区域列表与座位状态两个接口都不需要登录，用户也能在图书馆 h5 页面看到同样的数据。
本模块只读：不登录、不预约、不占座、不签到，也不发送任何 Cookie 或凭据。
"""

import json
import re
from datetime import datetime, timezone
from urllib.request import Request

from . import trace
from .result import command_usage, failure, success, wants_help, write_json
from .version import VERSION

DEFAULT_BASE = "http://libyy.qfnu.edu.cn"
REQUEST_TIMEOUT = 30
USER_AGENT = "easy-qfnu/" + VERSION
QUICK_SELECT_PATH = "/reserve/index/quickSelect"
SEAT_DATE_PATH = "/api/Seat/date"
SEAT_LIST_PATH = "/api/Seat/seat"

# 成功码按接口区分：quickSelect 用 "0"，座位接口用 "1"；上游可能回字符串也可能回数字。
QUICK_SELECT_OK = frozenset(["0"])
SEAT_OK = frozenset(["1"])
DEFAULT_START_TIME = "08:00"
DEFAULT_END_TIME = "22:00"

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")
DIGITS_RE = re.compile(r"^\d+$")

base = DEFAULT_BASE

SEAT_FIELDS = {
    "--area": "area",
    "--date": "date",
    "--segment": "segment",
    "--start-time": "startTime",
    "--end-time": "endTime",
}


class LibraryError(Exception):
    def __init__(self, message, hint=""):
        super().__init__(message)
        self.message = message
        self.hint = hint


class LibraryClientError(Exception):
    def __init__(self, message, hint):
        super().__init__(message)
        self.message = message
        self.hint = hint


def _action_areas(args, out):
    return run_library_areas(args, out)


def _action_seats(args, out):
    return run_library_seats(args, out)


# 单一来源：用法文本和分发都读这张表。加动作 = 加一行。
# 处理器签名统一为 (args, out)。
LIBRARY_COMMANDS = (
    {
        "name": "areas",
        "summary": "查校区 / 楼层 / 区域，并给出各区域空闲座位数（无需登录）",
        "extra": ("[--date YYYY-MM-DD]，默认取系统的可预约日期",),
        "run": _action_areas,
    },
    {
        "name": "seats",
        "summary": "查某区域某天的逐个座位状态（无需登录）",
        "extra": (
            "--area ID [--date YYYY-MM-DD] [--segment ID] [--free-only]",
            "[--start-time 08:00] [--end-time 22:00]",
        ),
        "run": _action_seats,
    },
)
LIBRARY_INDEX = {item["name"]: item for item in LIBRARY_COMMANDS}


def library_command(action):
    """按名字取命令表条目。未知动作返回 None。"""
    return LIBRARY_INDEX.get(action)


def run_library(args, out):
    if len(args) == 0 or wants_help(args):
        return usage_library(out)
    action = args[0]
    entry = library_command(action)
    if entry is None:
        hint = "支持 " + "、".join(item["name"] for item in LIBRARY_COMMANDS)
        return write_library_failure(out, "unknown action: " + action, hint)
    return entry["run"](args[1:], out)


def usage_library(out):
    try:
        out.write(command_usage("easy-qfnu library", LIBRARY_COMMANDS))
    except OSError:
        return 1
    return 2


def run_library_areas(args, out):
    try:
        values = parse_areas_args(args)
    except LibraryError as err:
        return write_library_failure(out, err.message, err.hint)
    try:
        response = quick_select(values["date"])
    except LibraryClientError as err:
        return write_library_failure(out, err.message, err.hint)
    if not code_ok(response["body"].get("code"), QUICK_SELECT_OK):
        message = response_message(response["body"], "图书馆系统拒绝了区域查询")
        return write_library_failure(out, message, "请稍后重试")
    return write_json(out, success("library", areas_result(response, values["date"])))


def run_library_seats(args, out):
    try:
        values = parse_seats_args(args)
    except LibraryError as err:
        return write_library_failure(out, err.message, err.hint)
    try:
        response = resolve_seats(values)
    except LibraryClientError as err:
        return write_library_failure(out, err.message, err.hint)
    if not code_ok(response["body"].get("code"), SEAT_OK):
        message = response_message(response["body"], "图书馆系统拒绝了座位查询")
        hint = "该区域或时段可能不可查询，请先用 easy-qfnu library areas 核对 --area 与日期"
        return write_library_failure(out, message, hint)
    return write_json(out, success("library", seats_result(response, values)))


def parse_areas_args(args):
    values = {"date": ""}
    index = 0
    while index < len(args):
        arg = args[index]
        if arg != "--date":
            if arg.startswith("-"):
                raise LibraryError("unknown option: " + arg)
            raise LibraryError("areas 不接受位置参数")
        if index + 1 >= len(args):
            raise LibraryError(arg + " requires a value")
        values["date"] = normalize_date(args[index + 1])
        index += 2
    return values


def parse_seats_args(args):
    values = {
        "area": "",
        "date": "",
        "segment": "",
        "startTime": DEFAULT_START_TIME,
        "endTime": DEFAULT_END_TIME,
        "freeOnly": False,
    }
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--free-only":
            values["freeOnly"] = True
            index += 1
            continue
        if arg not in SEAT_FIELDS:
            if arg.startswith("-"):
                raise LibraryError("unknown option: " + arg)
            raise LibraryError("seats 不接受位置参数")
        if index + 1 >= len(args):
            raise LibraryError(arg + " requires a value")
        values[SEAT_FIELDS[arg]] = normalize_seat_value(arg, args[index + 1])
        index += 2
    if values["area"] == "":
        raise LibraryError("seats 必须提供 --area", "先用 easy-qfnu library areas 查到区域 id")
    if values["date"] == "":
        values["date"] = local_today()
    return values


def normalize_seat_value(option, raw):
    value = raw.strip()
    if option == "--area":
        if not DIGITS_RE.match(value):
            raise LibraryError("--area 必须是数字 id", "先用 easy-qfnu library areas 查到区域 id")
        return value
    if option == "--segment":
        if not DIGITS_RE.match(value):
            raise LibraryError("--segment 必须是数字 id")
        return value
    if option == "--date":
        return normalize_date(value)
    if TIME_RE.match(value) is None:
        raise LibraryError(option + " 必须是 HH:MM 格式")
    return value


def normalize_date(raw):
    value = raw.strip()
    if DATE_RE.match(value) is None:
        raise LibraryError("--date 必须是 YYYY-MM-DD 格式")
    return value


def local_today():
    """本机当天日期（YYYY-MM-DD）。图书馆按时段分日，默认看当天。"""
    return datetime.now(timezone.utc).astimezone().date().isoformat()


def quick_select(day):
    """区域 / 楼层 / 区域查询。authorization 传空字符串，不登录。"""
    payload = {"id": "1", "date": day, "categoryIds": ["1"], "members": 0, "authorization": ""}
    return post_json(QUICK_SELECT_PATH, payload)


def resolve_seats(values):
    """解析时段并取座位列表：--segment 为空时先用 Seat/date 换当天的时段 id。"""
    segment = values["segment"]
    if segment == "":
        segment = pick_segment(fetch_segments(values["area"]), values["date"])
        if segment == "":
            message = "该区域在 " + values["date"] + " 没有可预约时段"
            raise LibraryClientError(message, "先用 easy-qfnu library areas 查看可预约日期")
    response = post_json(
        SEAT_LIST_PATH,
        {
            "area": int(values["area"]),
            "segment": int(segment),
            "day": values["date"],
            "startTime": values["startTime"],
            "endTime": values["endTime"],
        },
    )
    response["segment"] = segment
    return response


def fetch_segments(area):
    response = post_json(SEAT_DATE_PATH, {"build_id": int(area)})
    if not code_ok(response["body"].get("code"), SEAT_OK):
        message = response_message(response["body"], "无法读取该区域的预约时段")
        raise LibraryClientError(message, "请核对 --area 后重试")
    return response["body"].get("data")


def pick_segment(data, day):
    for entry in as_list(data):
        if not isinstance(entry, dict):
            continue
        if str(entry.get("day") or "") != day:
            continue
        for slot in as_list(entry.get("times")):
            if not isinstance(slot, dict):
                continue
            value = slot.get("id")
            if value not in (None, "", 0, "0"):
                return str(value)
    return ""


def post_json(path, payload):
    target = base.rstrip("/") + path
    request = Request(target, data=json.dumps(payload).encode("utf-8"), method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json, text/plain, */*")
    request.add_header("User-Agent", USER_AGENT)
    try:
        raw, status = read_response(request)
    except OSError as exc:
        raise LibraryClientError("图书馆系统请求失败", "请检查网络后重试") from exc
    return decode_body(raw, status, target)


def read_response(request):
    data, status, _final = trace.fetch(request, REQUEST_TIMEOUT)
    return data.decode("utf-8", errors="replace"), status


def decode_body(raw, status, target):
    try:
        body = json.loads(raw)
    except (TypeError, ValueError) as exc:
        trace.note("invalid JSON")
        raise LibraryClientError("图书馆系统返回了无效 JSON", "请稍后重试") from exc
    if not isinstance(body, dict):
        raise LibraryClientError("图书馆系统返回了无效 JSON", "请稍后重试")
    return {"status": status, "url": target, "body": body}


def code_ok(code, accepted):
    if code is None or isinstance(code, bool):
        return False
    return str(code).strip() in accepted


def response_message(body, fallback):
    message = body.get("msg")
    if not isinstance(message, str) or message.strip() == "":
        return fallback
    return message.strip()


def areas_result(response, requested_date):
    data = response["body"].get("data")
    if not isinstance(data, dict):
        data = {}
    areas = [normalize_node(item) for item in as_list(data.get("area"))]
    result = {
        "operation": "areas",
        "date": requested_date,
        "dates": as_list(data.get("date")),
        "campuses": [normalize_node(item) for item in as_list(data.get("premises"))],
        "floors": [normalize_node(item) for item in as_list(data.get("storey"))],
        "areas": areas,
        "area_count": len(areas),
        "total_num": sum_field(areas, "total_num"),
        "free_num": sum_field(areas, "free_num"),
        "url": response["url"],
    }
    return result


def normalize_node(item):
    if not isinstance(item, dict):
        return item
    node = dict(item)
    for key in ("total_num", "free_num"):
        node[key] = to_count(node.get(key))
    return node


def to_count(value):
    """total_num / free_num 上游可能是字符串也可能是数字，统一成整数，无法解析时原样返回。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, str) and DIGITS_RE.match(value.strip()):
        return int(value.strip())
    return value


def sum_field(items, key):
    total = 0
    for item in items:
        value = item.get(key) if isinstance(item, dict) else None
        if isinstance(value, int) and not isinstance(value, bool):
            total += value
    return total


def seats_result(response, values):
    raw_items = as_list(response["body"].get("data"))
    items = [seat_item(item) for item in raw_items if isinstance(item, dict)]
    free_items = [item for item in items if item["status"] == "1"]
    visible = free_items if values["freeOnly"] else items
    result = {
        "operation": "seats",
        "area": values["area"],
        "area_name": first_area_name(items),
        "day": values["date"],
        "segment": response["segment"],
        "start_time": values["startTime"],
        "end_time": values["endTime"],
        "free_only": values["freeOnly"],
        "seat_count": len(visible),
        "total_num": len(items),
        "free_num": len(free_items),
        "status_counts": status_counts(items),
        "items": visible,
        "url": response["url"],
    }
    return result


def seat_item(item):
    return {
        "id": as_text(item.get("id")),
        "no": as_text(item.get("no")),
        "name": as_text(item.get("name")),
        "status": as_text(item.get("status")),
        "status_name": as_text(item.get("status_name")),
        "area": as_text(item.get("area")),
        "area_name": as_text(item.get("area_name")),
    }


def first_area_name(items):
    for item in items:
        if item["area_name"] != "":
            return item["area_name"]
    return ""


def status_counts(items):
    counts = {}
    for item in items:
        key = item["status_name"] or item["status"] or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return {key: counts[key] for key in sorted(counts)}


def as_text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def as_list(value):
    if isinstance(value, list):
        return value
    return []


def write_library_failure(out, message, hint):
    return write_json(out, failure("library", message, hint)) + 1
