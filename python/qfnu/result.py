"""命令输出信封：成功/失败 JSON，保持与原 CLI 字段一致。"""

import json

from . import trace

HELP_FLAGS = ("--help", "-h")


def wants_help(args):
    """任意位置的 --help/-h 都算请求用法：先出用法，不发网络请求。"""
    return any(arg in HELP_FLAGS for arg in args)


def command_usage(program, commands, suffix="", footer=""):
    """按命令表渲染用法文本：program 例 "easy-qfnu jwc"。

    表项字段：name、summary（必填），extra（可选，参数说明续行）。
    """
    names = "|".join(item["name"] for item in commands)
    width = max(len(item["name"]) for item in commands) + 2
    lines = ["Usage: " + program + " <" + names + ">" + suffix]
    for item in commands:
        lines.append("  " + item["name"].ljust(width) + item["summary"])
        lines.extend(" " * (width + 2) + line for line in item.get("extra") or ())
    if footer:
        lines.append("  " + footer)
    return "\n".join(lines) + "\n"


def write_json(out, value):
    """把对象写成缩进 JSON 并换行。写出失败返回 1，成功返回 0。"""
    trace.attach(value)
    try:
        data = json.dumps(value, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return 1
    try:
        out.write(data + "\n")
    except OSError:
        return 1
    return 0


def success(source, fields):
    """组装 ok=true 的结果。fields 会覆盖到信封上。"""
    result = {"ok": True, "source": source}
    if fields:
        result.update(fields)
    return result


def failure(source, message, hint):
    """组装 ok=false 的结果。hint 为空时不写该字段。"""
    result = {"ok": False, "source": source, "error": message}
    if hint:
        result["hint"] = hint
    return result
