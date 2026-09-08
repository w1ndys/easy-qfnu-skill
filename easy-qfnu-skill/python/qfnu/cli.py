"""命令入口：解析子命令并写出 JSON。本轮只接通 version/help。"""

import sys

from .result import failure, success, write_json
from .version import VERSION


def usage(out):
    """打印用法。写出失败返回 1，成功返回 2（与原 CLI 一致）。"""
    try:
        out.write("Usage: easy-qfnu <version>\n")
    except OSError:
        return 1
    return 2


def run(args, stdout, stderr):
    """按参数分发命令。未知命令返回 JSON 失败，不访问校园站点。"""
    del stderr
    if len(args) == 0 or args[0] == "--help" or args[0] == "help":
        return usage(stdout)
    if args[0] == "version" or args[0] == "--version":
        return write_json(stdout, success("easy-qfnu", {"version": VERSION}))
    return write_json(
        stdout,
        failure("easy-qfnu", "unknown command: " + args[0], "run easy-qfnu --help"),
    )


def main(argv=None):
    """进程入口。argv 为 None 时使用 sys.argv[1:]。"""
    if argv is None:
        argv = sys.argv[1:]
    return run(argv, sys.stdout, sys.stderr)
