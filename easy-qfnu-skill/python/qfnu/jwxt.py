"""教务命令：验证码、登录、成绩、课表、评价、选课查询、状态、退出和忘记凭据。"""

import os

from . import telemetry
from .jwxt_auth import (
    captcha,
    clear_credentials_file,
    default_captcha_output,
    load_credentials_file,
    login,
    status,
)
from .jwxt_client import JWXTClient, JWXTError, default_credentials_path
from .jwxt_evaluation import evaluate, evaluations
from .jwxt_grades import grades
from .jwxt_schedule import schedule
from .jwxt_xk import run_jwxt_xk
from .result import failure, success, write_json

USAGE_ACTIONS = ("grades", "schedule", "evaluations", "evaluate")


def run_jwxt(args, out):
    if len(args) == 0 or args[0] == "--help":
        return usage_jwxt(out)
    if args[0] == "xk":
        return run_jwxt_xk(args[1:], out)
    if args[0] == "forget-credentials":
        command, err = parse_jwxt_command(args[0], args[1:])
        if err is not None:
            return write_json(out, failure("jwxt", str(err), ""))
        del command
        return run_forget_credentials(out)
    command, err = parse_jwxt_command(args[0], args[1:])
    if err is not None:
        return write_json(out, failure("jwxt", str(err), ""))
    try:
        client = prepare_client(command)
    except OSError as exc:
        return write_json(
            out,
            failure("jwxt", str(exc), "请检查本地会话文件；可运行 logout 清理损坏会话"),
        )
    if command["action"] == "logout":
        return run_logout(client, command["forget"], out)
    try:
        result = execute_jwxt(client, command)
    except JWXTError as exc:
        return write_jwxt_result(command["action"], None, exc, out)
    except OSError as exc:
        return write_jwxt_result(command["action"], None, exc, out)
    return write_jwxt_result(command["action"], result, None, out)


def usage_jwxt(out):
    try:
        out.write(
            "Usage: easy-qfnu jwxt <captcha|login|grades|schedule|evaluations|evaluate|status|logout|forget-credentials|xk>\n"
        )
    except OSError:
        return 1
    return 2


def parse_jwxt_command(action, args):
    command = {
        "action": action,
        "ocr_url": "",
        "session_path": "",
        "username": "",
        "password": "",
        "captcha": "",
        "output": "",
        "semester": "",
        "week": "",
        "mode": "",
        "score": 89,
        "courses": [],
        "confirm": False,
        "save": False,
        "save_set": False,
        "forget": False,
    }
    index = 0
    while index < len(args):
        try:
            consumed = parse_option(command, args[index:])
        except ValueError as exc:
            return None, exc
        index += consumed + 1
    apply_environment(command)
    return command, None


def parse_option(command, args):
    arg = args[0]
    if arg == "--confirm":
        command["confirm"] = True
        return 0
    if arg == "--forget-credentials" or arg == "--clear-credentials":
        command["forget"] = True
        return 0
    if arg == "--save-credentials":
        command["save_set"] = True
        command["save"] = True
        if len(args) > 1 and (args[1] == "yes" or args[1] == "no"):
            command["save"] = args[1] == "yes"
            return 1
        return 0
    if len(args) < 2:
        raise ValueError(arg + " requires a value")
    set_value_option(command, arg, args[1])
    return 1


def set_value_option(command, arg, value):
    if arg == "--ocr-url":
        command["ocr_url"] = value
    elif arg == "--session-path":
        command["session_path"] = value
    elif arg == "--username" or arg == "-u":
        command["username"] = value
    elif arg == "--password" or arg == "-p":
        command["password"] = value
    elif arg == "--captcha":
        command["captcha"] = value
    elif arg == "--out" or arg == "-o":
        command["output"] = value
    elif arg == "--semester" or arg == "--kksj" or arg == "--xnxq01id":
        command["semester"] = value
    elif arg == "--week" or arg == "--zc":
        command["week"] = value
    elif arg == "--kbjcmsid":
        command["mode"] = value
    elif arg == "--score" or arg == "--target-score":
        try:
            command["score"] = int(value)
        except ValueError:
            raise ValueError(arg + " must be an integer") from None
    elif arg == "--course":
        command["courses"].extend(value.split(","))
    else:
        raise ValueError("unknown option: " + arg)


def apply_environment(command):
    if command["ocr_url"] == "":
        command["ocr_url"] = os.environ.get("QFNU_OCR_URL") or ""
    if not command["save_set"] and os.environ.get("QFNU_JWXT_SAVE_CREDENTIALS", "").lower() == "yes":
        command["save"] = True


def prepare_client(command):
    client = JWXTClient(command["session_path"], command["ocr_url"])
    # captcha / 无验证码登录 / logout 会重建或删除会话，损坏文件不能挡住这些操作。
    needs_session = command["action"] != "captcha" and command["action"] != "logout" and (
        command["action"] != "login" or command["captcha"] != ""
    )
    if needs_session:
        client.load()
    return client


def run_forget_credentials(out):
    try:
        removed = clear_credentials_file()
    except OSError as exc:
        return write_json(out, failure("jwxt", str(exc), "请检查凭据文件权限"))
    return write_json(
        out,
        success("jwxt", {"credentials_removed": removed, "credentials_path": default_credentials_path()}),
    )


def run_logout(client, forget, out):
    try:
        client.clear()
    except OSError as exc:
        return write_json(out, failure("jwxt", str(exc), "请检查本地会话文件权限"))
    result = success("jwxt", {"logged_in": False, "session_path": client.session_path})
    if forget:
        try:
            removed = clear_credentials_file()
        except OSError as exc:
            return write_json(out, failure("jwxt", str(exc), "会话已清理；请检查凭据文件权限"))
        result["credentials_removed"] = removed
        result["credentials_path"] = default_credentials_path()
    return write_json(out, result)


def execute_jwxt(client, command):
    action = command["action"]
    if action == "captcha":
        output = command["output"] or default_captcha_output()
        return captcha(client, output)
    if action == "login":
        return login_jwxt(client, command)
    if action == "status" or action == "whoami":
        return status(client)
    if action == "grades":
        return grades(client, command["semester"])
    if action == "schedule":
        return schedule(client, command["semester"], command["week"], command["mode"])
    if action == "evaluations":
        return evaluations(client)
    if action == "evaluate":
        return evaluate(client, command["score"], command["courses"], command["confirm"])
    raise JWXTError("unknown action: " + action)


def login_jwxt(client, command):
    username = command["username"] or os.environ.get("QFNU_JWXT_USERNAME") or ""
    password = command["password"] or os.environ.get("QFNU_JWXT_PASSWORD") or ""
    if username == "" or password == "":
        file_user, file_password = load_credentials_file()
        username = file_user
        password = file_password
    return login(client, username, password, command["captcha"], command["save"])


def write_jwxt_result(action, result, err, out):
    if err is not None:
        if action in USAGE_ACTIONS:
            telemetry.report_usage("jwxt." + action, "failure")
        if isinstance(err, JWXTError):
            payload = failure("jwxt", err.message, err.hint)
        else:
            payload = failure("jwxt", str(err), "请检查网络和本地会话后重试")
        return write_json(out, payload)
    if action == "login":
        telemetry.report_login_success(result)
    elif action in USAGE_ACTIONS:
        telemetry.report_usage("jwxt." + action, "success")
    return write_json(out, result)
