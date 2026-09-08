"""新生入学考试题库检索。公开只读接口，不登录、不改题。"""

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import telemetry
from .result import failure, write_json

FRESHMAN_API = "https://freshman-exam.easy-qfnu.top/api/questions"
REQUEST_TIMEOUT = 30


class FreshmanError(Exception):
    def __init__(self, message, hint=""):
        super().__init__(message)
        self.message = message
        self.hint = hint


def parse_integer(option, value):
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(option + " must be an integer") from exc


def parse_freshman_search(args):
    if len(args) == 0 or args[0].strip() == "":
        raise ValueError("search keyword is empty")
    keyword = args[0].strip()
    page = 1
    page_size = 20
    index = 1
    while index < len(args):
        arg = args[index]
        if index + 1 >= len(args):
            raise ValueError(arg + " requires a value")
        if arg == "--page":
            page = parse_integer(arg, args[index + 1])
        elif arg == "--page-size":
            page_size = parse_integer(arg, args[index + 1])
        else:
            raise ValueError("unknown option: " + arg)
        index += 2
    if page < 1 or page_size < 1 or page_size > 100:
        raise ValueError("page must be >= 1 and page-size must be between 1 and 100")
    return {"keyword": keyword, "page": page, "page_size": page_size}


def parse_remote_error(upstream):
    ok = upstream.get("ok")
    if ok is not False:
        return None
    message = upstream.get("error")
    if not isinstance(message, str) or message.strip() == "":
        message = "question-bank request failed"
    hint = upstream.get("hint")
    if not isinstance(hint, str):
        hint = ""
    return FreshmanError(message, hint)


def normalize_freshman_response(upstream, target):
    result = dict(upstream)
    result["source"] = "freshman"
    if "page_size" not in result and "pageSize" in result:
        result["page_size"] = result["pageSize"]
    items = result.get("items")
    if isinstance(items, list):
        result["count"] = len(items)
    result["url"] = target
    return result


def query_freshman(search):
    query = urlencode(
        {
            "keyword": search["keyword"],
            "page": str(search["page"]),
            "pageSize": str(search["page_size"]),
        }
    )
    target = FRESHMAN_API + "?" + query
    try:
        raw, status = get_json_body(target)
    except OSError as exc:
        raise FreshmanError("failed to query question bank: " + str(exc), "请检查网络后重试") from exc
    if status < 200 or status >= 300:
        raise FreshmanError("question bank returned HTTP " + str(status), "请稍后重试")
    return parse_freshman_body(raw, target)


def parse_freshman_body(raw, target):
    try:
        upstream = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise FreshmanError(
            "invalid question-bank response",
            "请联系维护者并提供接口响应状态",
        ) from exc
    if not isinstance(upstream, dict):
        raise FreshmanError("invalid question-bank response", "请联系维护者并提供接口响应状态")
    remote = parse_remote_error(upstream)
    if remote is not None:
        raise remote
    return normalize_freshman_response(upstream, target)


def get_json_body(target):
    request = Request(target, method="GET")
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
        exc.read()
    except OSError as read_err:
        raise OSError("failed to read question-bank response") from read_err
    return "", exc.code


def run_freshman(args, out):
    if len(args) == 0 or args[0] == "--help":
        return usage_freshman(out)
    if args[0] != "search":
        return write_json(out, failure("freshman", "unknown action: " + args[0], ""))
    return run_freshman_search(args[1:], out)


def run_freshman_search(args, out):
    err = None
    result = None
    try:
        result = query_freshman(parse_freshman_search(args))
    except (FreshmanError, ValueError, OSError, TypeError) as caught:
        err = caught
    telemetry.report_usage("freshman.search", telemetry.usage_status(err))
    if err is not None:
        return write_json(out, freshman_failure(err))
    return write_json(out, result)


def freshman_failure(err):
    if isinstance(err, FreshmanError):
        return failure("freshman", err.message, err.hint)
    return failure("freshman", str(err), "")


def usage_freshman(out):
    try:
        out.write("Usage: easy-qfnu freshman search <keyword> [--page 1] [--page-size 20]\n")
    except OSError:
        return 1
    return 2
