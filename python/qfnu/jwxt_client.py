"""教务会话、Cookie 与 HTTP。登录跳转只跟随同源 302。"""

import json
import os
import urllib.error
import urllib.request
import urllib.response
from datetime import datetime, timezone
from http.cookiejar import Cookie, CookieJar
from urllib.parse import urljoin, urlparse

from . import trace

JWXT_BASE = "http://zhjw.qfnu.edu.cn"
CAPTCHA_URL = JWXT_BASE + "/verifycode.servlet"
SESS_URL = JWXT_BASE + "/Logon.do?method=logon&flag=sess"
LOGIN_URL = JWXT_BASE + "/Logon.do?method=logonLdap"
MAIN_URL = JWXT_BASE + "/jsxsd/framework/xsMain.jsp"
PROFILE_URL = JWXT_BASE + "/jsxsd/framework/xsMain_new.jsp?t1=1"
GRADE_URL = JWXT_BASE + "/jsxsd/kscj/cjcx_list"
SCHEDULE_URL = JWXT_BASE + "/jsxsd/xskb/xskb_list.do"
EVALUATION_FIND_URL = JWXT_BASE + "/jsxsd/xspj/xspj_find.do"
EVALUATION_SAVE_URL = JWXT_BASE + "/jsxsd/xspj/xspj_save.do"
PROGRAM_URL = JWXT_BASE + "/jsxsd/pyfa/topyfamx"
XK_LIST_URL = JWXT_BASE + "/jsxsd/xsxk/xklc_list"
XK_ENTER_URL = JWXT_BASE + "/jsxsd/xsxk/xsxk_index"
XK_SEARCH_ROOT = JWXT_BASE + "/jsxsd/xsxkkc"
REQUEST_TIMEOUT = 30
USER_AGENT = "easy-qfnu-skill/easy-qfnu"
ZERO_EXPIRES = "0001-01-01T00:00:00Z"

COOKIE_SCOPES = (
    ("/", JWXT_BASE + "/"),
    ("/Logon.do", JWXT_BASE + "/Logon.do"),
    ("/jsxsd", JWXT_BASE + "/jsxsd/framework/xsMain.jsp"),
)


class JWXTError(Exception):
    def __init__(self, message, hint=""):
        super().__init__(message)
        self.message = message
        self.hint = hint


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def http_error_302(self, req, fp, code, msg, headers):
        return _raw_response(req, fp, code, msg, headers)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


class SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, origin):
        super().__init__()
        self._origin = urlparse(origin)

    def http_error_302(self, req, fp, code, msg, headers):
        location = headers.get("Location") or headers.get("URI")
        if location:
            parsed = urlparse(urljoin(req.full_url, location.replace(" ", "%20")))
            if (
                parsed.username is None
                and parsed.scheme.lower() == self._origin.scheme.lower()
                and parsed.netloc.lower() == self._origin.netloc.lower()
            ):
                return super().http_error_302(req, fp, code, msg, headers)
        return _raw_response(req, fp, code, msg, headers)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


class SoftHTTPErrorProcessor(urllib.request.HTTPErrorProcessor):
    def http_response(self, request, response):
        code = getattr(response, "code", 0) or 0
        if 300 <= code < 400:
            return super().http_response(request, response)
        return response

    https_response = http_response


def _raw_response(req, fp, code, msg, headers):
    result = urllib.response.addinfourl(fp, headers, req.get_full_url(), code)
    result.msg = msg
    return result


def state_dir():
    value = os.environ.get("XDG_STATE_HOME", "").strip()
    if value:
        return os.path.join(value, "easy-qfnu-skill")
    home = os.path.expanduser("~")
    if home and home != "~":
        return os.path.join(home, ".local", "state", "easy-qfnu-skill")
    return os.path.join(".", ".local", "state", "easy-qfnu-skill")


def expand_path(path):
    if path.startswith("~/"):
        home = os.path.expanduser("~")
        if home and home != "~":
            return os.path.join(home, path[2:])
    return path


def default_session_path():
    value = os.environ.get("QFNU_JWXT_COOKIE_PATH") or os.environ.get("QFNU_JWXT_SESSION_PATH")
    if value:
        return expand_path(value)
    return os.path.join(state_dir(), "jwxt-session.json")


def default_credentials_path():
    value = os.environ.get("QFNU_JWXT_CREDENTIALS_PATH")
    if value:
        return expand_path(value)
    return os.path.join(state_dir(), "jwxt-credentials.json")


def default_captcha_path():
    return os.path.join(state_dir(), "jwxt-captcha.png")


def write_private_file(path, data, mode="wb"):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, mode=0o700, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, mode) as handle:
        handle.write(data)


def make_cookie(name, value, path, domain, secure, expires):
    host = domain or "zhjw.qfnu.edu.cn"
    return Cookie(
        0,
        name,
        value,
        None,
        False,
        host,
        bool(domain),
        host.startswith("."),
        path or "/",
        True,
        secure,
        expires,
        expires is None,
        None,
        None,
        {},
    )


def path_matches(cookie_path, request_path):
    if not cookie_path:
        cookie_path = "/"
    if not request_path.startswith(cookie_path):
        return False
    if cookie_path == "/" or cookie_path.endswith("/"):
        return True
    return len(request_path) == len(cookie_path) or request_path[len(cookie_path)] == "/"


def host_matches_cookie(cookie, host):
    domain = (cookie.domain or "").lower()
    if domain.startswith("."):
        domain = domain[1:]
        return host == domain or host.endswith("." + domain)
    if domain == "":
        return True
    return host == domain


def cookies_for_url(jar, url):
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path or "/"
    matched = []
    for cookie in jar:
        if not host_matches_cookie(cookie, host):
            continue
        if not path_matches(cookie.path or "/", path):
            continue
        matched.append(cookie)
    return matched


class JWXTClient:
    def __init__(self, session_path="", ocr_url=""):
        self.session_path = default_session_path()
        if session_path:
            self.session_path = expand_path(session_path)
        self.ocr_url = (ocr_url or "").rstrip("/")
        self.jar = CookieJar()
        self.meta = {}

    def load(self):
        try:
            with open(self.session_path, "r", encoding="utf-8") as handle:
                data = handle.read()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise OSError("read JWXT session: " + str(exc)) from exc
        try:
            saved = json.loads(data)
        except ValueError as exc:
            raise OSError("parse JWXT session: " + str(exc)) from exc
        if not isinstance(saved, dict):
            raise OSError("parse JWXT session: not an object")
        self.meta = saved
        for item in saved.get("cookies") or []:
            self.jar.set_cookie(_cookie_from_saved(item))

    def reset_jar(self):
        self.jar = CookieJar()
        self.meta = {}

    def persist(self, fields):
        apply_session_fields(self.meta, fields)
        self.meta["cookies"] = self.persisted_cookies()
        self.meta["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = json.dumps(session_file(self.meta), ensure_ascii=False, indent=2) + "\n"
        write_private_file(self.session_path, body, "w")

    def persisted_cookies(self):
        cookies = []
        seen = set()
        for path, url in COOKIE_SCOPES:
            for item in cookies_for_url(self.jar, url):
                key = item.name + "\0" + item.value
                if key in seen:
                    continue
                seen.add(key)
                cookies.append(saved_cookie(item, path))
        return cookies

    def clear(self):
        try:
            os.remove(self.session_path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise OSError("clear JWXT session: " + str(exc)) from exc
        self.reset_jar()

    def has_origin_cookies(self):
        return len(cookies_for_url(self.jar, JWXT_BASE + "/")) > 0

    def cookie_header(self):
        values = []
        for cookie in cookies_for_url(self.jar, JWXT_BASE + "/"):
            if cookie.name.strip() != "":
                values.append(cookie.name + "=" + cookie.value)
        return "; ".join(values)

    def request(self, method, target, body=None, headers=None, same_origin=False):
        origin = JWXT_BASE if same_origin else ""
        return self.request_with_origin(method, target, body, headers, origin)

    def request_with_origin(self, method, target, body=None, headers=None, origin=""):
        if origin:
            redirect = SameOriginRedirectHandler(origin)
        else:
            redirect = NoRedirectHandler()
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar),
            redirect,
            SoftHTTPErrorProcessor(),
        )
        request = urllib.request.Request(target, data=body, method=method)
        request.add_header("User-Agent", USER_AGENT)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            response = opener.open(request, timeout=REQUEST_TIMEOUT)
        except urllib.error.URLError as exc:
            trace.record(method, target, 0, "", "network error: " + str(exc.reason), body)
            raise OSError(str(exc.reason)) from exc
        try:
            data = response.read()
            status = getattr(response, "status", None) or response.getcode() or 0
            final_url = response.geturl() or target
        finally:
            response.close()
        trace.record(method, final_url, status, data, "", body)
        return status, final_url, data

    def text(self, method, target, body=None, headers=None, same_origin=False):
        status, final_url, data = self.request(method, target, body, headers, same_origin)
        return status, final_url, data.decode("utf-8", errors="replace")


def _cookie_from_saved(item):
    expires = parse_expires(item.get("Expires"))
    return make_cookie(
        item.get("Name") or "",
        item.get("Value") or "",
        item.get("Path") or "/",
        item.get("Domain") or "",
        bool(item.get("Secure")),
        expires,
    )


def parse_expires(value):
    if not value or value == ZERO_EXPIRES:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return int(parsed.timestamp())


def format_expires(expires):
    if not expires:
        return ZERO_EXPIRES
    return datetime.fromtimestamp(expires, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def saved_cookie(item, path):
    return {
        "Name": item.name,
        "Value": item.value,
        "Path": path,
        "Domain": item.domain or "",
        "Expires": format_expires(item.expires),
        "Secure": bool(item.secure),
    }


def apply_session_fields(meta, fields):
    for key, value in fields.items():
        if key == "username":
            if not isinstance(value, str):
                raise OSError("session field username must be a string")
            meta["username"] = value
        elif key == "captcha_pending":
            if not isinstance(value, bool):
                raise OSError("session field captcha_pending must be a boolean")
            meta["captcha_pending"] = value
        elif key == "profile":
            if not isinstance(value, dict):
                raise OSError("session field profile must be an object")
            meta["profile"] = value
        else:
            raise OSError("unsupported session field: " + key)


def session_file(meta):
    result = {"cookies": meta.get("cookies") or []}
    username = meta.get("username") or ""
    if username:
        result["username"] = username
    if meta.get("captcha_pending"):
        result["captcha_pending"] = True
    profile = meta.get("profile")
    if profile:
        result["profile"] = profile
    updated = meta.get("updated_at") or ""
    if updated:
        result["updated_at"] = updated
    return result
