import io
import unittest

from qfnu import freshman
from qfnu.cli import run
from qfnu.freshman import (
    normalize_freshman_response,
    parse_freshman_search,
    parse_remote_error,
)


class FreshmanTest(unittest.TestCase):
    def test_search_rejects_non_numeric_page(self):
        with self.assertRaises(ValueError) as ctx:
            parse_freshman_search(["校规", "--page", "later"])
        self.assertIn("--page must be an integer", str(ctx.exception))

    def test_search_rejects_page_size_out_of_range(self):
        with self.assertRaises(ValueError) as ctx:
            parse_freshman_search(["校规", "--page-size", "0"])
        self.assertIn("page-size must be between 1 and 100", str(ctx.exception))

    def test_normalize_adds_source_count_and_page_size(self):
        body = normalize_freshman_response(
            {"ok": True, "pageSize": 20, "items": [{"question": "校规"}]},
            "https://freshman-exam.easy-qfnu.top/api/questions?keyword=%E6%A0%A1%E8%A7%84",
        )
        self.assertEqual(body["source"], "freshman")
        self.assertEqual(body["page_size"], 20)
        self.assertEqual(body["count"], 1)
        self.assertIn("url", body)

    def test_remote_error_uses_upstream_message(self):
        err = parse_remote_error({"ok": False, "error": "empty keyword", "hint": "请输入关键词"})
        self.assertEqual(err.message, "empty keyword")
        self.assertEqual(err.hint, "请输入关键词")
        self.assertIsNone(parse_remote_error({"ok": True}))

    def test_run_reports_invalid_page_failure(self):
        out = io.StringIO()
        freshman.run_freshman(["search", "校规", "--page", "later"], out)
        self.assertIn("--page must be an integer", out.getvalue())

    def test_cli_dispatches_freshman_help(self):
        out = io.StringIO()
        code = run(["freshman", "--help"], out, io.StringIO())
        self.assertEqual(code, 2)
        text = out.getvalue()
        self.assertIn("easy-qfnu freshman", text)
        self.assertIn("--page-size 20", text)


if __name__ == "__main__":
    unittest.main()
