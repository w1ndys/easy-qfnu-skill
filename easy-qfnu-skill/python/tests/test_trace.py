import io
import json
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.request import Request

from qfnu import freshman, jwc, jwxt_auth, precourse, recommendation, telemetry, trace
from qfnu.cli import run
from qfnu.result import failure, success, write_json


class TraceHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        payload = b'{"oops":true,"detail":"upstream exploded"}'
        self.send_response(502)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        return


class EmptySuccessHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        payload = b'{"code":"OK","data":{"count":0,"courses":[]}}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        return


def start_server(handler):
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def stop_server(server):
    server.shutdown()
    server.server_close()


class TraceTest(unittest.TestCase):
    def setUp(self):
        trace.reset(False)

    def tearDown(self):
        trace.reset(False)

    def test_unknown_command_has_no_upstream(self):
        out = io.StringIO()
        run(["not-a-command"], out, io.StringIO())
        body = json.loads(out.getvalue())
        self.assertFalse(body["ok"])
        self.assertNotIn("upstream", body)
        self.assertNotIn("document", body)
        self.assertNotIn("document_url", body)

    def test_failure_keeps_full_raw_body(self):
        huge = "课表只有表头 " + ("x" * 5000)
        trace.record("GET", "http://zhjw.qfnu.edu.cn/jsxsd/xskb/xskb_list.do", 200, huge)
        trace.note("schedule table: 1 header row, 0 data rows")
        out = io.StringIO()
        write_json(out, failure("jwxt", "empty schedule", ""))
        body = json.loads(out.getvalue())
        upstream = body["upstream"]
        self.assertEqual(upstream["status"], 200)
        self.assertIn("xskb_list.do", upstream["url"])
        self.assertEqual(upstream["body"], huge)
        self.assertNotIn("truncated", upstream["body"])
        self.assertEqual(upstream["parse"], "schedule table: 1 header row, 0 data rows")
        self.assertNotIn("document", body)
        self.assertNotIn("document_url", body)

    def test_redacts_password_and_encoded(self):
        url = "http://zhjw.qfnu.edu.cn/Logon.do?encoded=SECRET&q=ok"
        raw = "userAccount=2023001&userPassword=hunter2&encoded=ABCDEF"
        exchange = trace.record("POST", url, 200, raw)
        self.assertIn("encoded=%5Bredacted%5D", exchange["url"])
        self.assertIn("q=ok", exchange["url"])
        self.assertNotIn("SECRET", exchange["url"])
        self.assertNotIn("hunter2", exchange["body_text"])
        self.assertNotIn("ABCDEF", exchange["body_text"])
        self.assertIn("userPassword=[redacted]", exchange["body_text"])
        out = io.StringIO()
        write_json(out, failure("jwxt", "login failed", ""))
        body = json.loads(out.getvalue())
        self.assertNotIn("hunter2", body["upstream"]["body"])
        self.assertNotIn("ABCDEF", body["upstream"]["body"])

    def test_binary_captcha_is_not_dumped(self):
        png = b"\x89PNG\r\n\x1a\n" + b"\x00\x01\x02"
        exchange = trace.record("GET", "http://zhjw.qfnu.edu.cn/verifycode.servlet", 200, png)
        self.assertEqual(exchange["body_text"], "[binary " + str(len(png)) + " bytes]")

    def test_debug_lists_all_exchanges(self):
        trace.reset(True)
        trace.record("GET", "http://zhjw.qfnu.edu.cn/a", 200, "first")
        trace.record("GET", "http://zhjw.qfnu.edu.cn/b", 502, "second")
        out = io.StringIO()
        write_json(out, failure("jwxt", "login failed", ""))
        body = json.loads(out.getvalue())
        self.assertEqual(body["upstream"]["body"], "second")
        self.assertEqual([item["body"] for item in body["exchanges"]], ["first", "second"])

    def test_success_hides_raw_body_without_debug(self):
        trace.record("GET", "http://zhjw.qfnu.edu.cn/xskb", 200, "<table>只有表头</table>")
        out = io.StringIO()
        write_json(out, success("jwxt", {"items": []}))
        body = json.loads(out.getvalue())
        self.assertTrue(body["ok"])
        self.assertNotIn("upstream", body)
        self.assertNotIn("exchanges", body)

    def test_success_debug_keeps_raw_body(self):
        trace.reset(True)
        trace.record("GET", "http://zhjw.qfnu.edu.cn/xskb", 200, "<table>只有表头</table>")
        out = io.StringIO()
        write_json(out, success("jwxt", {"items": []}))
        body = json.loads(out.getvalue())
        self.assertTrue(body["ok"])
        self.assertEqual(body["items"], [])
        self.assertEqual(body["upstream"]["status"], 200)
        self.assertIn("只有表头", body["upstream"]["body"])
        self.assertEqual(len(body["exchanges"]), 1)

    def test_success_command_debug_keeps_empty_payload(self):
        original = precourse.endpoint
        original_report = precourse.report_precourse_usage
        precourse.report_precourse_usage = lambda *_args: None
        server = start_server(EmptySuccessHandler)
        try:
            precourse.endpoint = "http://127.0.0.1:" + str(server.server_address[1]) + "/v1/precourse"
            out = io.StringIO()
            code = run(["--debug", "precourse", "search", "--course-name", "音乐"], out, io.StringIO())
        finally:
            stop_server(server)
            precourse.endpoint = original
            precourse.report_precourse_usage = original_report
        self.assertEqual(code, 0)
        body = json.loads(out.getvalue())
        self.assertTrue(body["ok"])
        self.assertEqual(body["count"], 0)
        self.assertIn('"courses":[]', body["upstream"]["body"].replace(" ", ""))

    @contextmanager
    def http_502(self):
        server = start_server(TraceHandler)
        original_usage = telemetry.report_usage
        telemetry.report_usage = lambda *_args: None
        try:
            yield "http://127.0.0.1:" + str(server.server_address[1])
        finally:
            stop_server(server)
            telemetry.report_usage = original_usage

    def assert_keeps_raw_body(self, args):
        out = io.StringIO()
        run(args, out, io.StringIO())
        body = json.loads(out.getvalue())
        self.assertFalse(body["ok"])
        self.assertEqual(body["upstream"]["status"], 502)
        self.assertIn("upstream exploded", body["upstream"]["body"])
        self.assertNotIn("document", body)

    def test_precourse_http_keeps_raw_body(self):
        original = precourse.endpoint
        original_report = precourse.report_precourse_usage
        precourse.report_precourse_usage = lambda *_args: None
        try:
            with self.http_502() as origin:
                precourse.endpoint = origin + "/v1/precourse"
                self.assert_keeps_raw_body(["precourse", "search", "--course-name", "音乐"])
        finally:
            precourse.endpoint = original
            precourse.report_precourse_usage = original_report

    def test_recommendation_http_keeps_raw_body(self):
        original = recommendation.endpoint
        original_report = recommendation.report_recommendation_usage
        recommendation.report_recommendation_usage = lambda *_args: None
        try:
            with self.http_502() as origin:
                recommendation.endpoint = origin + "/v1/recommendation"
                self.assert_keeps_raw_body(["recommendation", "search", "--course", "音乐"])
        finally:
            recommendation.endpoint = original
            recommendation.report_recommendation_usage = original_report

    def test_freshman_http_keeps_raw_body(self):
        original = freshman.FRESHMAN_API
        try:
            with self.http_502() as origin:
                freshman.FRESHMAN_API = origin + "/api/questions"
                self.assert_keeps_raw_body(["freshman", "search", "校规"])
        finally:
            freshman.FRESHMAN_API = original

    def test_jwc_http_keeps_raw_body(self):
        original = jwc.JWC_BASE
        try:
            with self.http_502() as origin:
                jwc.JWC_BASE = origin
                self.assert_keeps_raw_body(["jwc", "list"])
        finally:
            jwc.JWC_BASE = original

    def test_jwxt_http_keeps_raw_body(self):
        original_base = jwxt_auth.JWXT_BASE
        original_captcha = jwxt_auth.CAPTCHA_URL
        try:
            with self.http_502() as origin:
                jwxt_auth.JWXT_BASE = origin
                jwxt_auth.CAPTCHA_URL = origin + "/verifycode.servlet"
                self.assert_keeps_raw_body(["jwxt", "captcha"])
        finally:
            jwxt_auth.JWXT_BASE = original_base
            jwxt_auth.CAPTCHA_URL = original_captcha

    def test_debug_is_stripped_from_version(self):
        out = io.StringIO()
        code = run(["version", "--debug"], out, io.StringIO())
        self.assertEqual(code, 0)
        body = json.loads(out.getvalue())
        self.assertTrue(body["ok"])
        self.assertEqual(body["source"], "easy-qfnu")
        self.assertNotIn("exchanges", body)


class FetchTest(unittest.TestCase):
    def test_fetch_records_http_error(self):
        trace.reset(False)
        server = start_server(TraceHandler)
        try:
            port = server.server_address[1]
            data, status, _url = trace.fetch(
                Request("http://127.0.0.1:" + str(port) + "/fail"),
                5,
            )
            current = trace.last_exchange()
        finally:
            stop_server(server)
            trace.reset(False)
        self.assertEqual(status, 502)
        self.assertIn(b"exploded", data)
        self.assertEqual(current["status"], 502)
        self.assertIn("exploded", current["body_text"])


if __name__ == "__main__":
    unittest.main()
