"""教务命令：验证码、登录、成绩、课表、考试安排、评价、选课查询、状态、退出和忘记凭据。"""

import os
import sys

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
from .jwxt_exams import exams
from .jwxt_grades import grades
from .jwxt_program import program
from .jwxt_schedule import schedule
from .jwxt_xk import run_jwxt_xk
from .result import command_usage, failure, success, wants_help, write_json


def _action_captcha(client, command):
    return captcha(client, command["output"] or default_captcha_output())


def _action_login(client, command):
    return login_jwxt(client, command)


def _action_status(client, command):
    del command
    return status(client)


def _action_grades(client, command):
    return grades(client, command["semester"])


def _action_schedule(client, command):
    return schedule(client, command["semester"], command["week"], command["mode"])


def _action_exams(client, command):
    return exams(client, command["semester"], command["xqlb"])


def _action_program(client, command):
    return program(client, command["keyword"])


def _action_evaluations(client, command):
    del command
    return evaluations(client)


def _action_evaluate(client, command):
    return evaluate(client, command["score"], command["courses"], command["confirm"])


# 单一来源：用法文本和分发都读这张表。加动作 = 加一行。
# kind: action=需要会话的查询动作，session=清会话，credentials=删凭据，delegated=交给子模块。
JWXT_COMMANDS = (
    {"name": "captcha", "summary": "下载验证码图片", "kind": "action", "run": _action_captcha},
    {"name": "login", "summary": "用学号/密码/验证码登录", "kind": "action", "run": _action_login},
    {"name": "status", "summary": "登录状态与个人资料", "kind": "action", "run": _action_status},
    {"name": "grades", "summary": "查询成绩", "kind": "action", "run": _action_grades},
    {"name": "schedule", "summary": "查询课表", "kind": "action", "run": _action_schedule},
    {"name": "exams", "summary": "查询考试安排", "kind": "action", "run": _action_exams},
    {"name": "program", "summary": "查询培养方案与完成情况", "kind": "action", "run": _action_program},
    {"name": "evaluations", "summary": "查看待提交的教学评价", "kind": "action", "run": _action_evaluations},
    {"name": "evaluate", "summary": "提交教学评价（需 --confirm）", "kind": "action", "run": _action_evaluate},
    {"name": "logout", "summary": "清理本地会话（--forget-credentials 一并删凭据）", "kind": "session", "run": None},
    {"name": "forget-credentials", "summary": "只删除已保存凭据", "kind": "credentials", "run": None},
    {"name": "xk", "summary": "选课轮次即时查询（用法见 jwxt xk --help）", "kind": "delegated", "run": None},
)
JWXT_INDEX = {item["name"]: item for item in JWXT_COMMANDS}
JWXT_ALIASES = {"whoami": "status", "pyfa": "program"}


def jwxt_command(action):
    """按名字取命令表条目，先做别名归一。未知动作返回 None。"""
    return JWXT_INDEX.get(JWXT_ALIASES.get(action, action))


def run_jwxt(args, out, inp=None):
    if inp is None:
        inp = sys.stdin
    action = args[0] if len(args) > 0 else ""
    entry = jwxt_command(action)
    if entry is not None and entry["kind"] == "delegated":
        # xk 自带更细的用法，先交给它，别被 jwxt 这一层截胡。
        return run_jwxt_xk(args[1:], out)
    if len(args) == 0 or wants_help(args):
        return usage_jwxt(out)
    if action == "relay":
        return write_json(out, relay_offline())
    if entry is None:
        return write_json(
            out,
            failure("jwxt", "unknown action: " + action, "运行 easy-qfnu jwxt --help 查看支持的动作"),
        )
    command, err = parse_jwxt_command(action, args[1:])
    if err is not None:
        return write_json(out, failure("jwxt", str(err), ""))
    if entry["kind"] == "credentials":
        return run_forget_credentials(out)
    try:
        client = prepare_client(command)
    except OSError as exc:
        return write_json(
            out,
            failure("jwxt", str(exc), "请检查本地会话文件；可运行 logout 清理损坏会话"),
        )
    if entry["kind"] == "session":
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
            command_usage(
                "easy-qfnu jwxt",
                JWXT_COMMANDS,
                footer="别名：status=whoami，program=pyfa",
            )
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
        "keyword": "",
        "xqlb": "",
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
    elif arg == "--keyword" or arg == "-q":
        command["keyword"] = value
    elif arg == "--week" or arg == "--zc":
        command["week"] = value
    elif arg == "--kbjcmsid":
        command["mode"] = value
    elif arg == "--xqlb" or arg == "--term-category":
        command["xqlb"] = value
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
    entry = jwxt_command(command["action"])
    handler = None if entry is None else entry["run"]
    if handler is None:
        raise JWXTError("unknown action: " + command["action"])
    return handler(client, command)


def login_jwxt(client, command):
    username = command["username"] or os.environ.get("QFNU_JWXT_USERNAME") or ""
    password = command["password"] or os.environ.get("QFNU_JWXT_PASSWORD") or ""
    if username == "" or password == "":
        file_user, file_password = load_credentials_file()
        username = file_user
        password = file_password
    return login(client, username, password, command["captcha"], command["save"])


def relay_offline():
    """反馈与推荐提交的中转已下线：直接引导用户加群。"""
    return failure(
        "jwxt",
        "反馈与推荐提交暂时不可用",
        "请加入 QQ 群 1087015770（2 群，推荐）或 742726649（1 群）联系群主",
    )


def write_jwxt_result(action, result, err, out):
    if err is not None:
        if isinstance(err, JWXTError):
            payload = failure("jwxt", err.message, err.hint)
        else:
            payload = failure("jwxt", str(err), "请检查网络和本地会话后重试")
        return write_json(out, payload)
    return write_json(out, result)
