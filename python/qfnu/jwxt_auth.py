"""教务验证码、登录与本地凭据。"""

import base64
import html
import json
import os
import re
from urllib.parse import urlencode

from . import trace
from .jwxt_client import (
    CAPTCHA_URL,
    JWXT_BASE,
    LOGIN_URL,
    MAIN_URL,
    PROFILE_URL,
    SESS_URL,
    JWXTError,
    default_captcha_path,
    default_credentials_path,
    write_private_file,
)
from .result import success

SHOW_MSG_RE = re.compile(
    r'<([a-z][a-z0-9]*)\b[^>]*\bid\s*=\s*["\']showMsg["\'][^>]*>',
    re.IGNORECASE | re.DOTALL,
)
SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script\s*>", re.IGNORECASE | re.DOTALL)
STYLE_RE = re.compile(r"<style\b[^>]*>.*?</style\s*>", re.IGNORECASE | re.DOTALL)
BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>", re.IGNORECASE | re.DOTALL)
PROFILE_LABELS = {
    "学生姓名": "name",
    "姓名": "name",
    "学生编号": "student_id",
    "学号": "student_id",
    "所属院系": "college",
    "专业名称": "major",
    "班级名称": "class_name",
}


def encode_credentials(username, password, scode, sxh):
    raw = username + "%%%" + password
    chars = list(raw)
    out = []
    scode_pos = 0
    for index, char in enumerate(chars):
        if index >= 20:
            out.append("".join(chars[index:]))
            break
        out.append(char)
        if index >= len(sxh):
            continue
        count = ord(sxh[index]) - ord("0")
        if count <= 0 or scode_pos >= len(scode):
            continue
        end = min(scode_pos + count, len(scode))
        out.append(scode[scode_pos:end])
        scode_pos = end
    return "".join(out)


def contains_any(text, markers):
    for marker in markers:
        if marker in text:
            return True
    return False


def strip_tags(raw):
    raw = SCRIPT_RE.sub(" ", raw)
    raw = STYLE_RE.sub(" ", raw)
    raw = BR_RE.sub(" ", raw)
    raw = TAG_RE.sub(" ", raw)
    return " ".join(raw.split())


def parse_login_message(raw):
    match = SHOW_MSG_RE.search(raw)
    if match is None:
        return ""
    tag = match.group(1)
    end_re = re.compile(r"</\s*" + re.escape(tag) + r"\s*>", re.IGNORECASE | re.DOTALL)
    end = end_re.search(raw, match.end())
    if end is None:
        return ""
    message = html.unescape(strip_tags(raw[match.end() : end.start()]))
    return " ".join(message.split())


def login_failure_hint(message):
    if contains_any(message, ["验证码错误", "验证码不正确"]):
        return "重新运行 easy-qfnu jwxt captcha 获取新验证码"
    if contains_any(message, ["密码错误", "用户名或密码错误", "用户名密码错误", "用户名或者密码有误"]):
        return "核对学号和学校服务大厅密码，不要重复提交错误密码"
    if contains_any(message, ["其他地方登录", "别处登录", "异地登录"]):
        return "账号已在其他地方登录，请先退出已有会话后再重试"
    return "请根据教务系统返回的错误核对登录信息后重试"


def validate_login_response(raw):
    message = parse_login_message(raw)
    if message:
        trace.note("login showMsg")
        raise JWXTError(message, login_failure_hint(message))
    if contains_any(raw, ["密码错误", "用户名或密码错误", "用户名密码错误", "您提供的用户名或者密码有误"]):
        raise JWXTError(
            "username or password is wrong",
            "核对学号和学校服务大厅密码，不要重复提交错误密码",
        )
    if contains_any(raw, ["验证码错误", "验证码不正确"]):
        raise JWXTError("captcha rejected by 教务系统", "重新运行 easy-qfnu jwxt captcha 获取新验证码")


def parse_profile(raw):
    profile = {}
    plain = strip_tags(raw)
    for label, key in PROFILE_LABELS.items():
        index = plain.find(label)
        if index < 0:
            continue
        value = plain[index + len(label) :].strip().lstrip(" :：\t")
        fields = value.split()
        if fields:
            profile[key] = fields[0]
    return profile


def merge_profile(dst, src):
    for key, value in src.items():
        if isinstance(value, str) and value != "":
            dst[key] = value


def fetch_captcha(client):
    status, _, data = client.request("GET", CAPTCHA_URL)
    if status != 200 or not data:
        raise OSError("captcha image HTTP " + str(status))
    return data


def init_session(client):
    status, _, _ = client.request("GET", JWXT_BASE + "/")
    if status >= 400:
        raise OSError("session init HTTP " + str(status))


def captcha(client, output):
    client.reset_jar()
    init_session(client)
    image = fetch_captcha(client)
    path = os.path.abspath(output)
    write_private_file(path, image, "wb")
    client.persist({"captcha_pending": True})
    return success(
        "jwxt",
        {
            "captcha_image_path": path,
            "session_path": client.session_path,
            "next": "easy-qfnu jwxt login --username <学号> --password <密码> --captcha <识图结果>",
            "hint": "请用模型或用户读取验证码；验证码错误时重新运行 jwxt captcha",
        },
    )


def recognize(client, image):
    if client.ocr_url == "":
        raise JWXTError("OCR URL is not configured", "设置 QFNU_OCR_URL 或使用 jwxt login --captcha")
    form = urlencode({"image": base64.b64encode(image).decode("ascii")}).encode("utf-8")
    status, _, data = client.request(
        "POST",
        client.ocr_url + "/ocr",
        form,
        {"Content-Type": "application/x-www-form-urlencoded"},
    )
    if status != 200:
        raise OSError("OCR HTTP " + str(status))
    try:
        body = json.loads(data.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise OSError("OCR returned invalid JSON") from exc
    code = body.get("code")
    code_text = "<nil>" if code is None else str(code)
    result = body.get("data") or ""
    if result == "" or (code_text != "200" and code_text != "0" and code_text != "<nil>"):
        raise OSError("OCR rejected captcha: " + str(body.get("message") or ""))
    return result.strip()


def prepare_login_captcha(client, captcha_text):
    if captcha_text:
        return captcha_text
    client.reset_jar()
    init_session(client)
    image = fetch_captcha(client)
    try:
        return recognize(client, image)
    except JWXTError:
        raise
    except OSError as exc:
        raise JWXTError(
            str(exc),
            "部署独立 ddddocr 服务，或运行 easy-qfnu jwxt captcha 后手动传入验证码",
        ) from exc


def login_session_codes(client):
    if not client.has_origin_cookies():
        raise JWXTError(
            "no active captcha session",
            "先运行 easy-qfnu jwxt captcha，再用 --captcha 提交识别结果",
        )
    status, _, raw = client.text(
        "POST",
        SESS_URL,
        b"",
        {"Content-Type": "application/x-www-form-urlencoded"},
    )
    if status >= 400:
        raise JWXTError("failed to obtain scode/sxh")
    parts = raw.strip().split("#", 1)
    if len(parts) != 2 or parts[0] == "" or parts[1] == "":
        raise JWXTError("invalid scode/sxh")
    return parts[0], parts[1]


def submit_login(client, username, password, captcha_text):
    scode, sxh = login_session_codes(client)
    encoded = encode_credentials(username, password, scode, sxh)
    form = urlencode(
        {
            "userAccount": "",
            "userPassword": "",
            "RANDOMCODE": captcha_text,
            "encoded": encoded,
        }
    ).encode("utf-8")
    _, _, body = client.text(
        "POST",
        LOGIN_URL,
        form,
        {"Content-Type": "application/x-www-form-urlencoded"},
        True,
    )
    return body


def enrich_profile(client, profile):
    try:
        status, _, body = client.text("GET", PROFILE_URL)
    except OSError as exc:
        return "个人资料补全请求失败：" + str(exc)
    if status != 200:
        return "个人资料补全返回 HTTP " + str(status)
    merge_profile(profile, parse_profile(body))
    return ""


def record_credential_save(result, username, password, enabled):
    result["credentials_saved"] = False
    if not enabled:
        return
    try:
        save_credentials_file(username, password)
    except OSError as exc:
        result["hint"] = str(exc)
        return
    result["credentials_saved"] = True
    result["credentials_path"] = default_credentials_path()


def build_login_result(client, username, password, save_credentials):
    status, _, main = client.text("GET", MAIN_URL)
    if status != 200 or not contains_any(main, ["教学一体化服务平台", "glyphicon-class"]):
        raise JWXTError("login failed: success marker missing on xsMain.jsp")
    profile = parse_profile(main)
    warning = enrich_profile(client, profile)
    client.persist({"username": username, "captcha_pending": False, "profile": profile})
    result = success(
        "jwxt",
        {
            "logged_in": True,
            "username": username,
            "profile": profile,
            "main_url": MAIN_URL,
            "session_path": client.session_path,
            "captcha": "vision",
        },
    )
    if warning:
        result["profile_warning"] = warning
    if client.ocr_url:
        result["ocr_url"] = client.ocr_url
    record_credential_save(result, username, password, save_credentials)
    return result


def login(client, username, password, captcha_text, save_credentials):
    username = username.strip()
    if username == "" or password == "":
        raise JWXTError(
            "username/password required",
            "传入 --username/--password 或设置 QFNU_JWXT_USERNAME/QFNU_JWXT_PASSWORD",
        )
    prepared = prepare_login_captcha(client, captcha_text)
    body = submit_login(client, username, password, prepared)
    validate_login_response(body)
    return build_login_result(client, username, password, save_credentials)


def status(client):
    if not client.has_origin_cookies():
        return success(
            "jwxt",
            {
                "logged_in": False,
                "session_path": client.session_path,
                "hint": "run easy-qfnu jwxt login first",
            },
        )
    try:
        code, final_url, main = client.text("GET", MAIN_URL)
    except OSError as exc:
        return success(
            "jwxt",
            {"logged_in": False, "session_path": client.session_path, "error": str(exc)},
        )
    if (
        code != 200
        or contains_any(main, ["请输入账号", "请输入密码", "请输入验证码"])
        or not contains_any(main, ["教学一体化服务平台", "glyphicon-class"])
    ):
        return expired_status(client)
    profile = parse_profile(main)
    warning = enrich_profile(client, profile)
    client.persist({"profile": profile, "username": client.meta.get("username") or ""})
    result = success(
        "jwxt",
        {
            "logged_in": True,
            "username": client.meta.get("username") or "",
            "profile": profile,
            "main_url": final_url,
            "session_path": client.session_path,
        },
    )
    if warning:
        result["profile_warning"] = warning
    return result


def expired_status(client):
    if client.ocr_url:
        username, password = load_credentials_file()
        if username and password:
            try:
                result = login(client, username, password, "", False)
                result["auto_relogin"] = True
                return result
            except (JWXTError, OSError):
                pass
    hint = "run easy-qfnu jwxt login again"
    if client.ocr_url == "" and client.meta.get("username"):
        hint = "会话已过期且未配置 QFNU_OCR_URL；请运行 easy-qfnu jwxt captcha，再用 easy-qfnu jwxt login --captcha 提交识别结果"
    return success("jwxt", {"logged_in": False, "session_path": client.session_path, "hint": hint})


def load_credentials_file():
    path = default_credentials_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = handle.read()
    except FileNotFoundError:
        return "", ""
    except OSError as exc:
        raise OSError("read credentials: " + str(exc)) from exc
    try:
        value = json.loads(data)
    except ValueError as exc:
        raise OSError("parse credentials: " + str(exc)) from exc
    return value.get("username") or "", value.get("password") or ""


def save_credentials_file(username, password):
    path = default_credentials_path()
    body = json.dumps({"username": username, "password": password}, ensure_ascii=False, indent=2) + "\n"
    write_private_file(path, body, "w")


def clear_credentials_file():
    try:
        os.remove(default_credentials_path())
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise OSError("remove credentials: " + str(exc)) from exc
    return True


def default_captcha_output():
    return default_captcha_path()
