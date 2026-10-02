import inspect
import io
import json
import re
import unittest

from qfnu import cli, freshman, jwc, jwxt, jwxt_xk, library, precourse, recommendation
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
            (["library", "areas", "--help"], "easy-qfnu library"),
            (["library", "seats", "-h"], "easy-qfnu library"),
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

    def test_family_usage_comes_from_command_table(self):
        families = (
            (jwxt, jwxt.usage_jwxt, jwxt.JWXT_COMMANDS, jwxt.jwxt_command),
            (jwc, jwc.usage_jwc, jwc.JWC_COMMANDS, jwc.jwc_command),
            (freshman, freshman.usage_freshman, freshman.FRESHMAN_COMMANDS, freshman.freshman_command),
            (
                precourse,
                precourse.usage_precourse,
                precourse.PRECOURSE_COMMANDS,
                precourse.precourse_command,
            ),
            (
                recommendation,
                recommendation.usage_recommendation,
                recommendation.RECOMMENDATION_COMMANDS,
                recommendation.recommendation_command,
            ),
            (
                library,
                library.usage_library,
                library.LIBRARY_COMMANDS,
                library.library_command,
            ),
        )
        for module, usage, table, lookup in families:
            with self.subTest(family=module.__name__):
                out = io.StringIO()
                usage(out)
                self.assertEqual(usage_actions(out.getvalue()), {item["name"] for item in table})
                for item in table:
                    self.assertIs(lookup(item["name"]), item)
                    if item.get("kind", "action") == "action":
                        self.assertIsNotNone(item["run"])
                self.assertIsNone(lookup("not-a-command"))

    def test_jwxt_table_resolves_aliases(self):
        self.assertIs(jwxt.jwxt_command("whoami"), jwxt.jwxt_command("status"))
        self.assertIs(jwxt.jwxt_command("pyfa"), jwxt.jwxt_command("program"))

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
