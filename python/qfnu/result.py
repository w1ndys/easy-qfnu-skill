"""命令输出信封：成功/失败 JSON，保持与原 CLI 字段一致。"""

import json

from . import trace

HELP_FLAGS = ("--help", "-h")


def wants_help(args):
    """任意位置的 --help/-h 都算请求用法：先出用法，不发网络请求。"""
    return any(arg in HELP_FLAGS for arg in args)


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
