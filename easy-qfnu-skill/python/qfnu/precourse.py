"""预选课公开缓存查询。不登录教务系统，不提交选课。"""

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import telemetry
from .result import failure, success, write_json
from .version import VERSION

DEFAULT_ENDPOINT = "https://precourse.easy-qfnu.top/v1/precourses"
REQUEST_TIMEOUT = 30
endpoint = DEFAULT_ENDPOINT

SEARCH_OPTIONS = {
    "--q": "q",
    "-q": "q",
    "--course-code": "courseCode",
    "--course-name": "courseName",
    "--teacher-name": "teacherName",
    "--course-nature": "courseNature",
    "--course-attr": "courseAttr",
    "--college": "college",
    "--schedule-time": "scheduleTime",
    "--location": "location",
    "--campus": "campus",
}


class PrecourseError(Exception):
    def __init__(self, message, hint=""):
        super().__init__(message)
        self.message = message
        self.hint = hint


class PrecourseClientError(Exception):
    def __init__(self, message, hint, report):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.report = report


def report_precourse_usage(operation, status):
    telemetry.report_usage("precourse." + operation, status)


def run_precourse(args, out):
    if len(args) == 0 or args[0] == "--help":
        return usage_precourse(out)
    action = args[0]
    if action == "search":
        return run_precourse_search(args[1:], out)
    if action == "meta":
        if len(args) > 1:
            return write_precourse_failure(out, "meta 不接受额外参数", "")
        return request_precourse("meta", None, out)
    if action == "popular":
        return run_precourse_popular(args[1:], out)
    return write_precourse_failure(out, "unknown action: " + action, "支持 search、meta、popular")


def usage_precourse(out):
    text = (
        "Usage: easy-qfnu precourse <search|meta|popular>\n"
        "  easy-qfnu precourse search [keyword] [--course-code value] [--course-name value] [--teacher-name value]\n"
        "    [--course-nature value] [--course-attr value] [--college value] [--schedule-time value]\n"
        "    [--location value] [--campus value]\n"
        "  easy-qfnu precourse meta\n"
        "  easy-qfnu precourse popular --field <teacherName|courseName|college>\n"
    )
    try:
        out.write(text)
    except OSError:
        return 1
    return 2


def run_precourse_search(args, out):
    try:
        values = parse_search_args(args)
    except PrecourseError as err:
        if err.message == "help":
            return usage_precourse(out)
        return write_precourse_failure(out, err.message, err.hint)
    return request_precourse("search", values, out)


def parse_search_args(args):
    values = {}
    keyword = ""
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--help":
            raise PrecourseError("help")
        field = SEARCH_OPTIONS.get(arg)
        if field is not None:
            values, keyword, index = take_search_option(args, index, field, values, keyword)
            continue
        if arg.startswith("-"):
            raise PrecourseError("unknown option: " + arg)
        if keyword != "":
            raise PrecourseError("search 只接受一个位置关键词")
        keyword = arg.strip()
        index += 1
    return finish_search_values(values, keyword)


def take_search_option(args, index, field, values, keyword):
    arg = args[index]
    if index + 1 >= len(args):
        raise PrecourseError(arg + " requires a value")
    value = args[index + 1].strip()
    if field == "q" and keyword != "":
        raise PrecourseError("搜索关键词只能指定一次")
    values[field] = value
    return values, keyword, index + 2


def finish_search_values(values, keyword):
    if keyword != "":
        if values.get("q"):
            raise PrecourseError("搜索关键词只能指定一次")
        values["q"] = keyword
    cleaned = {}
    for key, value in values.items():
        trimmed = value.strip()
        if trimmed != "":
            cleaned[key] = trimmed
    if len(cleaned) == 0:
        raise PrecourseError("至少提供一个非空查询条件")
    return cleaned


def run_precourse_popular(args, out):
    try:
        field = parse_popular_field(args)
    except PrecourseError as err:
        if err.message == "help":
            return usage_precourse(out)
        return write_precourse_failure(out, err.message, err.hint)
    return request_precourse("popular", {"field": field}, out)


def parse_popular_field(args):
    field = ""
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--help":
            raise PrecourseError("help")
        if arg != "--field":
            raise PrecourseError("unknown option: " + arg, "使用 --field 指定统计字段")
        if field != "":
            raise PrecourseError("--field 只能指定一次")
        if index + 1 >= len(args):
            raise PrecourseError("--field requires a value")
        field = args[index + 1].strip()
        index += 2
    if field != "teacherName" and field != "courseName" and field != "college":
        raise PrecourseError("field 必须是 teacherName、courseName 或 college")
    return field


def request_precourse(operation, values, out):
    try:
        response = query_precourse(operation, values)
    except PrecourseClientError as err:
        if err.report:
            report_precourse_usage(operation, "failure")
        return write_precourse_failure(out, err.message, err.hint)
    return finish_precourse_response(operation, response, out)


def finish_precourse_response(operation, response, out):
    if response["status"] < 200 or response["status"] >= 300:
        message = response_message(response["body"], "预选课服务返回 HTTP " + str(response["status"]))
        report_precourse_usage(operation, "failure")
        return write_precourse_failure(out, message, "请稍后重试")
    if not is_success_code(response["body"].get("code")):
        message = response_message(response["body"], "预选课服务拒绝了查询请求")
        report_precourse_usage(operation, "failure")
        return write_precourse_failure(out, message, "请检查查询条件后重试")
    report_precourse_usage(operation, "success")
    return write_json(out, success("precourse", precourse_result(operation, response)))


def response_message(body, fallback):
    message = body.get("message")
    if not isinstance(message, str) or message.strip() == "":
        return fallback
    return message.strip()


def precourse_result(operation, response):
    data = response["body"].get("data")
    if isinstance(data, dict):
        result = dict(data)
    else:
        result = {"data": data}
    result["operation"] = operation
    result["url"] = response["url"]
    return result


def is_success_code(code):
    if isinstance(code, bool):
        return False
    if isinstance(code, (int, float)):
        return code == 0
    if isinstance(code, str):
        return code == "0" or code == "OK"
    return False


def query_precourse(operation, values):
    target = endpoint.rstrip("/") + "/" + operation
    if values:
        target = target + "?" + urlencode(values)
    request = Request(target, method="GET")
    request.add_header("Accept", "application/json")
    request.add_header("User-Agent", "easy-qfnu/" + VERSION)
    try:
        raw, status = read_response(request)
    except OSError as exc:
        raise PrecourseClientError("预选课查询请求失败", "请检查网络和远程服务后重试", True) from exc
    return decode_precourse_body(raw, status, target)


def decode_precourse_body(raw, status, target):
    try:
        body = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise PrecourseClientError("预选课服务返回了无效 JSON", "请稍后重试", True) from exc
    if not isinstance(body, dict):
        raise PrecourseClientError("预选课服务返回了无效 JSON", "请稍后重试", True)
    return {"status": status, "url": target, "body": body}


def read_response(request):
    try:
        response = urlopen(request, timeout=REQUEST_TIMEOUT)
    except HTTPError as exc:
        return read_http_error(exc)
    except URLError as exc:
        raise OSError(str(exc.reason)) from exc
    try:
        data = response.read()
        status = response.getcode() or 0
    finally:
        response.close()
    return data.decode("utf-8", errors="replace"), status


def read_http_error(exc):
    try:
        data = exc.read()
    except OSError as read_err:
        raise OSError("failed to read precourse response") from read_err
    return data.decode("utf-8", errors="replace"), exc.code


def write_precourse_failure(out, message, hint):
    return write_json(out, failure("precourse", message, hint)) + 1
