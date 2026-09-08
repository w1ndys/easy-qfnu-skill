"""公开选课推荐查询。不登录教务系统，不提交评价。"""

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import telemetry
from .result import failure, success, write_json
from .version import VERSION

DEFAULT_ENDPOINT = "https://recommend.easy-qfnu.top/v1/recommendation"
REQUEST_TIMEOUT = 30
endpoint = DEFAULT_ENDPOINT


class RecommendationError(Exception):
    def __init__(self, message, hint=""):
        super().__init__(message)
        self.message = message
        self.hint = hint


class RecommendationClientError(Exception):
    def __init__(self, message, hint, report):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.report = report


def report_recommendation_usage(operation, status):
    telemetry.report_usage("recommendation." + operation, status)


def run_recommendation(args, out):
    if len(args) == 0 or args[0] == "--help":
        return usage_recommendation(out)
    if args[0] == "search":
        return run_recommendation_search(args[1:], out)
    return write_recommendation_failure(out, "unknown action: " + args[0], "支持 search")


def usage_recommendation(out):
    try:
        out.write("Usage: easy-qfnu recommendation search [--course value] [--teacher value] [--top 20]\n")
    except OSError:
        return 1
    return 2


def run_recommendation_search(args, out):
    try:
        values = parse_search_args(args)
    except RecommendationError as err:
        if err.message == "help":
            return usage_recommendation(out)
        return write_recommendation_failure(out, err.message, err.hint)
    return request_recommendation(values, out)


def parse_search_args(args):
    course = ""
    teacher = ""
    top = 20
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--help":
            raise RecommendationError("help")
        if arg != "--course" and arg != "--teacher" and arg != "--top":
            if arg.startswith("-"):
                raise RecommendationError("unknown option: " + arg)
            raise RecommendationError("search 不接受位置参数")
        if index + 1 >= len(args):
            raise RecommendationError(arg + " requires a value")
        value = args[index + 1].strip()
        index += 2
        if arg == "--course":
            course = value
        elif arg == "--teacher":
            teacher = value
        else:
            top = parse_top(value)
    if course == "" and teacher == "":
        raise RecommendationError("至少提供一个非空 --course 或 --teacher")
    if top < 1 or top > 100:
        raise RecommendationError("top 必须是 1 到 100 的整数")
    values = {"top": str(top)}
    if course != "":
        values["course"] = course
    if teacher != "":
        values["teacher"] = teacher
    return values


def parse_top(value):
    try:
        return int(value)
    except ValueError as exc:
        raise RecommendationError("--top must be an integer") from exc


def request_recommendation(values, out):
    try:
        response = query_recommendations(values)
    except RecommendationClientError as err:
        if err.report:
            report_recommendation_usage("search", "failure")
        return write_recommendation_failure(out, err.message, err.hint)
    return finish_recommendation_response(response, out)


def finish_recommendation_response(response, out):
    if response["status"] < 200 or response["status"] >= 300:
        message = response_message(response["body"], "推荐服务返回 HTTP " + str(response["status"]))
        report_recommendation_usage("search", "failure")
        return write_recommendation_failure(out, message, "请稍后重试")
    if not is_success_code(response["body"].get("code")):
        message = response_message(response["body"], "推荐服务拒绝了查询请求")
        report_recommendation_usage("search", "failure")
        return write_recommendation_failure(out, message, "请检查查询条件后重试")
    report_recommendation_usage("search", "success")
    return write_json(out, success("recommendation", recommendation_result(response)))


def response_message(body, fallback):
    message = body.get("message")
    if not isinstance(message, str) or message.strip() == "":
        return fallback
    return message.strip()


def recommendation_result(response):
    data = response["body"].get("data")
    if isinstance(data, dict):
        result = dict(data)
    else:
        result = {"data": data}
    result["operation"] = "search"
    result["url"] = response["url"]
    return result


def is_success_code(code):
    return isinstance(code, str) and code == "OK"


def query_recommendations(values):
    target = endpoint.rstrip("/")
    if values:
        target = target + "?" + urlencode(values)
    request = Request(target, method="GET")
    request.add_header("Accept", "application/json")
    request.add_header("User-Agent", "easy-qfnu/" + VERSION)
    try:
        raw, status = read_response(request)
    except OSError as exc:
        raise RecommendationClientError("推荐查询请求失败", "请检查网络和远程服务后重试", True) from exc
    return decode_body(raw, status, target)


def decode_body(raw, status, target):
    try:
        body = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise RecommendationClientError("推荐服务返回了无效 JSON", "请稍后重试", True) from exc
    if not isinstance(body, dict):
        raise RecommendationClientError("推荐服务返回了无效 JSON", "请稍后重试", True)
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
        raise OSError("failed to read recommendation response") from read_err
    return data.decode("utf-8", errors="replace"), exc.code


def write_recommendation_failure(out, message, hint):
    return write_json(out, failure("recommendation", message, hint)) + 1
