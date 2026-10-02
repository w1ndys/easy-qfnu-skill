import inspect
import io
import json
import re
import unittest

from qfnu import cli, jwc, jwxt, jwxt_xk
from qfnu.cli import run
from qfnu.version import VERSION


class CliTest(unittest.TestCase):
    def test_version_returns_ok_json(self):
        out = io.StringIO()
        code = run(["version"], out, io.StringIO())
        self.assertEqual(code, 0)
        body = json.loads(out.getvalue())
        self.assertTrue(body["ok"])
        self.assertEqual(body["source"], "easy-qfnu")
        self.assertEqual(body["version"], VERSION)

    def test_unknown_command_returns_error_json(self):
        out = io.StringIO()
        code = run(["not-a-command"], out, io.StringIO())
        self.assertEqual(code, 0)
        body = json.loads(out.getvalue())
        self.assertFalse(body["ok"])
        self.assertIn("unknown command", body["error"])

    def test_help_prints_usage(self):
        out = io.StringIO()
        code = run(["--help"], out, io.StringIO())
        self.assertEqual(code, 2)
        self.assertIn("Usage:", out.getvalue())

    def test_subcommand_help_prints_usage(self):
        cases = (
            (["-h"], "easy-qfnu"),
            (["--help"], "easy-qfnu"),
            (["jwxt", "grades", "--help"], "easy-qfnu jwxt"),
            (["jwxt", "login", "-h"], "easy-qfnu jwxt"),
            (["jwxt", "status", "--help"], "easy-qfnu jwxt"),
            (["jwxt", "xk", "search", "--help"], "jwxt xk"),
            (["jwc", "list", "--help"], "easy-qfnu jwc"),
            (["jwc", "get", "--help"], "easy-qfnu jwc"),
            (["jwc", "search", "--help"], "easy-qfnu jwc"),
            (["jwc", "channels", "--help"], "easy-qfnu jwc"),
            (["freshman", "search", "--help"], "easy-qfnu freshman"),
            (["precourse", "meta", "--help"], "easy-qfnu precourse"),
            (["precourse", "popular", "-h"], "easy-qfnu precourse"),
            (["recommendation", "search", "--help"], "easy-qfnu recommendation"),
        )
        for args, expected in cases:
            out = io.StringIO()
            code = run(args, out, io.StringIO())
            text = out.getvalue()
            self.assertEqual(code, 2, args)
            self.assertIn("Usage:", text, args)
            self.assertIn(expected, text, args)


def usage_actions(text):
    """从 usage 文本里取出 <a|b|c> 形式的子命令列表。"""
    return set(re.search(r"<([^<>|]+(?:\|[^<>|]+)*)>", text).group(1).split("|"))


def dispatched_actions(func, pattern):
    """读分发函数源码里的字符串比较：用法文本和分发表必须一致，别两处各写一遍。"""
    return set(re.findall(pattern, inspect.getsource(func)))


class UsageDriftTest(unittest.TestCase):
    """用法文本里的子命令列表 = 实际能分发的子命令。"""

    def test_jwxt_usage_comes_from_command_table(self):
        out = io.StringIO()
        jwxt.usage_jwxt(out)
        self.assertEqual(
            usage_actions(out.getvalue()),
            {item["name"] for item in jwxt.JWXT_COMMANDS},
        )

    def test_jwxt_command_table_resolves_names_and_aliases(self):
        for item in jwxt.JWXT_COMMANDS:
            self.assertIs(jwxt.jwxt_command(item["name"]), item)
            if item["kind"] == "action":
                self.assertIsNotNone(item["run"])
        self.assertIs(jwxt.jwxt_command("whoami"), jwxt.jwxt_command("status"))
        self.assertIs(jwxt.jwxt_command("pyfa"), jwxt.jwxt_command("program"))
        self.assertIsNone(jwxt.jwxt_command("not-a-command"))

    def test_jwc_usage_matches_dispatch(self):
        out = io.StringIO()
        jwc.usage_jwc(out)
        self.assertEqual(
            usage_actions(out.getvalue()),
            dispatched_actions(jwc.dispatch_jwc, r'action == "([a-z-]+)"'),
        )

    def test_xk_usage_matches_parser(self):
        out = io.StringIO()
        jwxt_xk.usage_xk(out)
        self.assertEqual(
            usage_actions(out.getvalue()),
            dispatched_actions(jwxt_xk.parse_xk_command, r'action != "([a-z-]+)"'),
        )

    def test_usage_lists_every_family(self):
        out = io.StringIO()
        run(["--help"], out, io.StringIO())
        self.assertEqual(
            usage_actions(out.getvalue()),
            {name for name, _ in cli.FAMILIES} | {"version"},
        )


if __name__ == "__main__":
    unittest.main()
