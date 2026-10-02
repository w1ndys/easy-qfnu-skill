"""命令入口：解析子命令并写出 JSON。"""

import sys

from . import trace
from .freshman import run_freshman
from .jwc import run_jwc
from .jwxt import run_jwxt
from .library import run_library
from .precourse import run_precourse
from .recommendation import run_recommendation
from .result import HELP_FLAGS, failure, success, write_json
from .version import VERSION

# 单一来源：顶层用法和分发都读这张表。加命令族 = 加一行。
FAMILIES = (
    ("jwc", run_jwc),
    ("freshman", run_freshman),
    ("precourse", run_precourse),
    ("recommendation", run_recommendation),
    ("library", run_library),
    ("jwxt", run_jwxt),
)
FAMILY_RUNNERS = dict(FAMILIES)


def usage(out):
    """打印用法。写出失败返回 1，成功返回 2（与原 CLI 一致）。"""
    names = "|".join([name for name, _ in FAMILIES] + ["version"])
    try:
        out.write("Usage: easy-qfnu [--debug] <" + names + ">\n")
    except OSError:
        return 1
    return 2


def run(args, stdout, stderr):
    """按参数分发命令。未知命令返回 JSON 失败。"""
    del stderr
    debug, args = trace.take_debug_flag(args)
    trace.reset(debug)
    if len(args) == 0 or args[0] in HELP_FLAGS or args[0] == "help":
        return usage(stdout)
    if args[0] == "version" or args[0] == "--version":
        return write_json(stdout, success("easy-qfnu", {"version": VERSION}))
    runner = FAMILY_RUNNERS.get(args[0])
    if runner is not None:
        return runner(args[1:], stdout)
    return write_json(
        stdout,
        failure("easy-qfnu", "unknown command: " + args[0], "run easy-qfnu --help"),
    )


def main(argv=None):
    """进程入口。argv 为 None 时使用 sys.argv[1:]。"""
    if argv is None:
        argv = sys.argv[1:]
    return run(argv, sys.stdout, sys.stderr)
