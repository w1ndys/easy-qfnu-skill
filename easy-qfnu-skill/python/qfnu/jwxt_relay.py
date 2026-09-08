"""把登录 Cookie 中继到固定远程服务。stdin 必须是 JSON。"""

import hashlib
import json
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request

from . import telemetry, trace
from .jwxt_client import REQUEST_TIMEOUT, JWXTClient
from .result import failure, write_json
from .version import VERSION

RELAY_TARGETS = {
    "feedback": {
        "endpoint": "https://hub.easy-qfnu.top/v1/feedback",
        "method": "POST",
        "sends_cookie": True,
        "sends_idempotency": True,
    },
    "recommendation": {
        "endpoint": "https://hub.easy-qfnu.top/v1/recommendation-submission",
        "method": "POST",
        "sends_cookie": True,
        "sends_idempotency": True,
    },
    "rank": {
        "endpoint": "https://ranking.easy-qfnu.top/v1/ranking/me",
        "method": "GET",
        "sends_cookie": True,
        "sends_idempotency": False,
    },
}


class RelayError(Exception):
    def __init__(self, message, hint=""):
        super().__init__(message)
        self.message = message
        self.hint = hint


class RelayClientError(Exception):
    def __init__(self, reading_response=False):
        super().__init__("relay request failed")
        self.reading_response = reading_response


def run_jwxt_relay_command(args, out, inp):
    if len(args) != 2:
        return write_json(
            out,
            failure("jwxt", "relay requires one fixed action", "支持 feedback、recommendation、rank"),
        )
    try:
        client = JWXTClient()
        client.load()
    except OSError as exc:
        return write_json(out, failure("jwxt", str(exc), "请检查本地会话文件"))
    return run_jwxt_relay(args[1], client, inp, out)


def run_jwxt_relay(action, client, inp, out):
    target = RELAY_TARGETS.get(action)
    if target is None:
        return relay_failure(out, "unknown relay action: " + action, "支持 feedback、recommendation、rank")
    try:
        body = read_relay_input(inp)
        request = new_relay_http_request(target, client, body)
    except RelayError as exc:
        return relay_failure(out, exc.message, exc.hint)
    try:
        status, data = do_relay_request(request)
    except RelayClientError as exc:
        telemetry.report_usage("relay." + action, "failure")
        if exc.reading_response:
            return relay_failure(out, "failed to read relay response", "请稍后重试")
        return relay_failure(out, "relay request failed", "请检查网络和远程服务后重试")
    telemetry.report_usage("relay." + action, telemetry.usage_status(relay_status_error(status)))
    return write_relay_response(status, data, out)


def relay_status_error(status):
    if status < 200 or status >= 300:
        return OSError("relay HTTP " + str(status))
    return None


def read_relay_input(inp):
    try:
        body = inp.read()
    except OSError as exc:
        raise RelayError("failed to read relay input", "请通过标准输入提供 JSON") from exc
    if isinstance(body, str):
        text = body.strip()
        raw = text.encode("utf-8")
    else:
        raw = body.strip()
        text = raw.decode("utf-8", errors="replace")
    try:
        json.loads(text)
    except ValueError as exc:
        raise RelayError("relay input must be valid JSON", "请通过标准输入提供 JSON 对象") from exc
    return raw


def relay_method(target):
    method = target.get("method") or ""
    if method == "":
        return "POST"
    return method


def relay_idempotency_key(body):
    return hashlib.sha256(body).hexdigest()


def rank_query(endpoint, parsed):
    extra = set(parsed.keys()) - {"scope", "course_codes"}
    if extra:
        raise ValueError("rank input must be a JSON object with scope and course_codes")
    scope = parsed.get("scope")
    if scope is None:
        scope = ""
    elif not isinstance(scope, str):
        raise ValueError("rank input must be a JSON object with scope and course_codes")
    codes = parsed.get("course_codes")
    if codes is None:
        codes = []
    elif not isinstance(codes, list):
        raise ValueError("rank input must be a JSON object with scope and course_codes")
    query = urlparse(endpoint)
    values = list(parse_qsl(query.query, keep_blank_values=True))
    if scope != "":
        values.append(("scope", scope))
    for code in codes:
        if not isinstance(code, str) or code.strip() == "":
            raise ValueError("course_codes cannot contain empty values")
        values.append(("course_code", code))
    return urlunparse((query.scheme, query.netloc, query.path, query.params, urlencode(values), query.fragment))


def relay_request(target, body):
    if relay_method(target) != "GET":
        return target["endpoint"], body
    try:
        parsed = json.loads(body.decode("utf-8"))
    except ValueError as exc:
        raise ValueError("rank input must be a JSON object with scope and course_codes") from exc
    if not isinstance(parsed, dict):
        raise ValueError("rank input must be a JSON object with scope and course_codes")  # noqa: TRY004
    return rank_query(target["endpoint"], parsed), b""


def new_relay_http_request(target, client, body):
    try:
        endpoint, request_body = relay_request(target, body)
    except ValueError as exc:
        raise RelayError(str(exc), "请提供符合该操作约定的 JSON 对象") from exc
    data = request_body if request_body else None
    request = Request(endpoint, data=data, method=relay_method(target))
    request.add_header("Accept", "application/json")
    if request_body:
        request.add_header("Content-Type", "application/json")
    request.add_header("User-Agent", "easy-qfnu/" + VERSION)
    if target.get("sends_cookie"):
        cookie = client.cookie_header()
        if cookie == "":
            raise RelayError("no active JWXT session", "请先运行 easy-qfnu jwxt login")
        request.add_header("X-QFNU-JWXT-Cookie", cookie)
    if target.get("sends_idempotency"):
        request.add_header("Idempotency-Key", relay_idempotency_key(body))
    return request


def do_relay_request(request):
    try:
        data, status, _final = trace.fetch(request, REQUEST_TIMEOUT)
    except OSError as exc:
        raise RelayClientError() from exc
    return status, data


def write_relay_response(status, data, out):
    if not data.strip():
        return relay_failure(out, "relay HTTP " + str(status), "远程服务没有返回 JSON")
    try:
        out.write(data.decode("utf-8", errors="replace"))
        out.write("\n")
    except OSError:
        return 1
    if status < 200 or status >= 300:
        return 1
    return 0


def relay_failure(out, message, hint):
    write_json(out, failure("jwxt", message, hint))
    return 1
