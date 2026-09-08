import io
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from qfnu import precourse
from qfnu.cli import run

ORIGINAL_ENDPOINT = precourse.endpoint
ORIGINAL_REPORT = precourse.report_precourse_usage
SERVER_STATE = {"status": 200, "body": "{}", "paths": [], "auth": ""}


class PrecourseHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        SERVER_STATE["paths"].append(self.path)
        SERVER_STATE["auth"] = self.headers.get("Authorization", "")
        payload = SERVER_STATE["body"].encode("utf-8")
        self.send_response(SERVER_STATE["status"])
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        return


def start_server():
    server = HTTPServer(("127.0.0.1", 0), PrecourseHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def stop_server(server):
    server.shutdown()
    server.server_close()


def capture_usage():
    events = []

    def fake(operation, status):
        events.append(operation + ":" + status)

    precourse.report_precourse_usage = fake
    return events


class PrecourseTest(unittest.TestCase):
    def setUp(self):
        SERVER_STATE["status"] = 200
        SERVER_STATE["body"] = "{}"
        SERVER_STATE["paths"] = []
        SERVER_STATE["auth"] = ""

    def tearDown(self):
        precourse.endpoint = ORIGINAL_ENDPOINT
        precourse.report_precourse_usage = ORIGINAL_REPORT

    def test_search_builds_query_and_returns_courses(self):
        events = capture_usage()
        SERVER_STATE["body"] = (
            '{"code":"OK","data":{"count":1,"courses":[{"courseCode":"590014","courseName":"音乐鉴赏"}]}}'
        )
        server = start_server()
        try:
            port = server.server_address[1]
            precourse.endpoint = "http://127.0.0.1:" + str(port) + "/v1/precourses"
            out = io.StringIO()
            code = precourse.run_precourse_search(
                ["音乐鉴赏", "--campus", "日照", "--teacher-name", "王老师"],
                out,
            )
        finally:
            stop_server(server)
        self.assertEqual(code, 0)
        parsed = urlparse(SERVER_STATE["paths"][0])
        query = parse_qs(parsed.query)
        self.assertEqual(parsed.path, "/v1/precourses/search")
        self.assertEqual(query.get("q"), ["音乐鉴赏"])
        self.assertEqual(query.get("campus"), ["日照"])
        self.assertEqual(query.get("teacherName"), ["王老师"])
        self.assertEqual(SERVER_STATE["auth"], "")
        body = json.loads(out.getvalue())
        self.assertTrue(body["ok"])
        self.assertEqual(body["source"], "precourse")
        self.assertEqual(body["count"], 1)
        self.assertEqual(len(body["courses"]), 1)
        self.assertEqual(events, ["search:success"])

    def test_meta_popular_and_cli_dispatch(self):
        events = capture_usage()
        SERVER_STATE["body"] = '{"code":0,"data":{"items":[]}}'
        server = start_server()
        try:
            port = server.server_address[1]
            precourse.endpoint = "http://127.0.0.1:" + str(port) + "/v1/precourses"
            out = io.StringIO()
            self.assertEqual(precourse.run_precourse(["meta"], out), 0)
            out = io.StringIO()
            self.assertEqual(precourse.run_precourse(["popular", "--field", "college"], out), 0)
            out = io.StringIO()
            self.assertEqual(run(["precourse", "search", "音乐"], out, io.StringIO()), 0)
        finally:
            stop_server(server)
        self.assertEqual(
            SERVER_STATE["paths"],
            [
                "/v1/precourses/meta",
                "/v1/precourses/popular?field=college",
                "/v1/precourses/search?q=%E9%9F%B3%E4%B9%90",
            ],
        )
        self.assertEqual(events, ["meta:success", "popular:success", "search:success"])

        out = io.StringIO()
        run(["precourses", "search", "音乐"], out, io.StringIO())
        body = json.loads(out.getvalue())
        self.assertFalse(body["ok"])
        self.assertIn("unknown command: precourses", body["error"])

    def test_rejects_missing_conditions_and_remote_failure(self):
        events = capture_usage()
        out = io.StringIO()
        code = precourse.run_precourse_search(["--campus", " "], out)
        self.assertNotEqual(code, 0)
        self.assertIn("非空", out.getvalue())
        self.assertEqual(events, [])

        SERVER_STATE["status"] = 502
        SERVER_STATE["body"] = '{"code":"UPSTREAM_UNAVAILABLE","data":null}'
        server = start_server()
        try:
            port = server.server_address[1]
            precourse.endpoint = "http://127.0.0.1:" + str(port)
            out = io.StringIO()
            code = precourse.run_precourse_search(["音乐"], out)
        finally:
            stop_server(server)
        self.assertNotEqual(code, 0)
        self.assertIn("HTTP 502", out.getvalue())
        self.assertEqual(events, ["search:failure"])


if __name__ == "__main__":
    unittest.main()
