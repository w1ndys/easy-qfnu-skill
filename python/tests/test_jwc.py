import io
import json
import unittest

from qfnu import jwc, telemetry
from qfnu.cli import run
from qfnu.jwc import JWC_BASE, parse_list_items

ORIGINAL_REPORT_USAGE = telemetry.report_usage


def capture_usage():
    events = []

    def fake(feature, status):
        events.append(feature + ":" + status)

    telemetry.report_usage = fake
    return events


class JWCTest(unittest.TestCase):
    def tearDown(self):
        telemetry.report_usage = ORIGINAL_REPORT_USAGE

    def test_parse_list_items(self):
        raw = (
            '<ul class="n_listxx1"><li><h2><a href="info/1103/7719.htm" title="通知标题">'
            '通知标题</a><span class="time">2026-07-13</span></h2><p>摘要内容</p></li></ul>'
        )
        items = parse_list_items(raw, JWC_BASE + "/tz_j_.htm")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "7719")
        self.assertEqual(items[0]["category_id"], "1103")
        self.assertEqual(items[0]["title"], "通知标题")
        self.assertEqual(items[0]["date"], "2026-07-13")

    def test_list_rejects_non_numeric_page(self):
        with self.assertRaises(ValueError) as ctx:
            jwc.list_jwc(["--page", "later"])
        self.assertIn("--page must be an integer", str(ctx.exception))

    def test_search_rejects_non_numeric_limit(self):
        with self.assertRaises(ValueError) as ctx:
            jwc.search_jwc(["选课", "--limit", "many"])
        self.assertIn("--limit must be an integer", str(ctx.exception))

    def test_run_reports_only_network_actions(self):
        events = capture_usage()
        out = io.StringIO()
        jwc.run_jwc(["list", "--page", "later"], out)
        self.assertIn('"ok": false', out.getvalue())
        self.assertEqual(events, ["jwc.list:failure"])

        events.clear()
        jwc.run_jwc(["channels"], out)
        self.assertEqual(events, [])

        events.clear()
        jwc.run_jwc(["bogus"], out)
        self.assertEqual(events, [])

    def test_get_refuses_non_jwc_url(self):
        events = capture_usage()
        out = io.StringIO()
        jwc.run_jwc(["get", "https://example.com/x.htm"], out)
        body = json.loads(out.getvalue())
        self.assertFalse(body["ok"])
        self.assertIn("refusing non-JWC URL", body["error"])
        self.assertEqual(events, ["jwc.get:failure"])

    def test_cli_dispatches_jwc_channels(self):
        events = capture_usage()
        out = io.StringIO()
        code = run(["jwc", "channels"], out, io.StringIO())
        self.assertEqual(code, 0)
        body = json.loads(out.getvalue())
        self.assertTrue(body["ok"])
        self.assertGreaterEqual(len(body["channels"]), 1)
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
