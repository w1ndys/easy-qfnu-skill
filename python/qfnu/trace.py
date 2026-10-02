"""全局请求证据。jwc/freshman/precourse/recommendation/jwxt 等用户 HTTP 都走这里。

失败时 JSON 带完整上游正文；--debug 时成功也带。脱敏、不截断。登录/会话入口（/Logon.do）的请求与
响应正文一律不进 JSON，只留字节数。不写文档、不发链接、不上报 hub。
"""

import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import urlopen

REDACT_KEYS = {
    "password",
    "userpassword",
    "useraccount",
    "encoded",
    "authorization",
    "cookie",
    "randomcode",
}
FORM_RE = re.compile(
    r"(?i)(password|userPassword|userAccount|encoded|RANDOMCODE|Authorization|Cookie)=[^&]*"
)


INPUT_TAG_RE = re.compile(r"<input\b[^>]*>", re.IGNORECASE | re.DOTALL)
ATTR_NAME_RE = re.compile(r"(?i)\b(?:id|name)\s*=\s*[\"']?([A-Za-z0-9_\-]+)")
VALUE_ATTR_RE = re.compile(r"(?i)\bvalue\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)")

debug_enabled = False
_exchanges = []


def env_debug():
    value = os.environ.get("QFNU_DEBUG", "").strip().lower()
    return value in ("1", "true", "yes", "on")


def take_debug_flag(args):
    enabled = env_debug()
    rest = []
    for arg in args:
        if arg == "--debug":
            enabled = True
        else:
            rest.append(arg)
    return enabled, rest


def reset(enabled=False):
    global debug_enabled
    debug_enabled = enabled
    _exchanges.clear()


def last_exchange():
    if not _exchanges:
        return None
    return _exchanges[-1]


def note(parse):
    current = last_exchange()
    if current is None:
        return
    current["parse"] = parse


def record(method, url, status, body, parse="", request_body=None, sensitive=False):
    """记录一次上游交换。sensitive 用于登录/会话入口：正文只留字节数。"""
    raw = as_bytes(body)
    exchange = {
        "method": method,
        "url": sanitize_url(url),
        "status": status,
        "body_bytes": len(raw),
        "body_text": redacted_credentials(raw, "response") if sensitive else sanitize_body(raw),
    }
    if parse:
        exchange["parse"] = parse
    if request_body:
        sent = as_bytes(request_body)
        if sensitive:
            exchange["request_text"] = redacted_credentials(sent, "request")
        else:
            exchange["request_text"] = sanitize_body(sent)
    _exchanges.append(exchange)
    return exchange


def redacted_credentials(raw, kind):
    return "[redacted credentials " + kind + ", " + str(len(raw)) + " bytes]"


def as_bytes(body):
    if isinstance(body, (bytes, bytearray)):
        return bytes(body)
    if body is None:
        return b""
    return str(body).encode("utf-8", errors="replace")


def public_upstream(exchange):
    result = {
        "method": exchange.get("method") or "",
        "url": exchange.get("url") or "",
        "status": exchange.get("status") or 0,
        "body_bytes": exchange.get("body_bytes") or 0,
        "body": exchange.get("body_text") or "",
    }
    parse = exchange.get("parse") or ""
    if parse:
        result["parse"] = parse
    return result


def attach(result):
    if not isinstance(result, dict):
        return
    current = last_exchange()
    if current is None:
        return
    failed = result.get("ok") is False
    if failed or debug_enabled:
        result["upstream"] = public_upstream(current)
    if debug_enabled:
        result["exchanges"] = [public_upstream(item) for item in _exchanges]


def fetch(request, timeout):
    method = request.get_method()
    url = request.full_url
    try:
        response = urlopen(request, timeout=timeout)
    except HTTPError as exc:
        data, status = read_http_error(exc)
        record(method, url, status, data)
        return data, status, url
    except URLError as exc:
        record(method, url, 0, "", "network error: " + str(exc.reason))
        raise OSError(str(exc.reason)) from exc
    try:
        data = response.read()
        status = response.getcode() or 0
        final_url = response.geturl() or url
    finally:
        response.close()
    record(method, final_url, status, data)
    return data, status, final_url


def read_http_error(exc):
    try:
        data = exc.read()
    except OSError:
        data = b""
    return data, exc.code


def sanitize_url(url):
    parts = urlsplit(url)
    if parts.query == "":
        return url
    pairs = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() in REDACT_KEYS:
            pairs.append((key, "[redacted]"))
        else:
            pairs.append((key, value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(pairs), parts.fragment))


def sanitize_body(raw):
    if looks_binary(raw):
        return "[binary " + str(len(raw)) + " bytes]"
    text = raw.decode("utf-8", errors="replace")
    return redact_input_values(FORM_RE.sub(r"\1=[redacted]", text))


def redact_input_values(text):
    """兜底脱敏 HTML 属性回显：<input id="userPassword" value="明文密码">。"""

    def replace(match):
        tag = match.group(0)
        names = [item.group(1).lower() for item in ATTR_NAME_RE.finditer(tag)]
        if not any(name in REDACT_KEYS for name in names):
            return tag
        return VALUE_ATTR_RE.sub('value="[redacted]"', tag)

    return INPUT_TAG_RE.sub(replace, text)


def looks_binary(raw):
    if raw.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF")):
        return True
    return b"\x00" in raw[:1024]
