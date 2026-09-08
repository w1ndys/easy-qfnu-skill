import io
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from qfnu import recommendation
from qfnu.cli import run

ORIGINAL_ENDPOINT = recommendation.endpoint
ORIGINAL_REPORT = recommendation.report_recommendation_usage
SERVER_STATE = {"status": 200, "body": "{}", "paths": [], "auth": "", "cookie": ""}


class RecommendationHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        SERVER_STATE["paths"].append(self.path)
        SERVER_STATE["auth"] = self.headers.get("Authorization", "")
        SERVER_STATE["cookie"] = self.headers.get("Cookie", "")
        payload = SERVER_STATE["body"].encode("utf-8")
        self.send_response(SERVER_STATE["status"])
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        return


def start_server():
    server = HTTPServer(("127.0.0.1", 0), RecommendationHandler)
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

    recommendation.report_recommendation_usage = fake
    return events


class RecommendationTest(unittest.TestCase):
    def setUp(self):
        SERVER_STATE["status"] = 200
        SERVER_STATE["body"] = "{}"
        SERVER_STATE["paths"] = []
        SERVER_STATE["auth"] = ""
        SERVER_STATE["cookie"] = ""

    def tearDown(self):
        recommendation.endpoint = ORIGINAL_ENDPOINT
        recommendation.report_recommendation_usage = ORIGINAL_REPORT

    def test_search_builds_query_and_returns_items(self):
        events = capture_usage()
        SERVER_STATE["body"] = (
            '{"code":"OK","data":{"count":1,"updated_at":"2026-09-08T00:00:00Z","version":"abc",'
            '"items":[{"course_name":"高等数学","teacher_name":"张老师","year":"2025-2026",'
            '"reason":"讲解清楚","nickname":null}]}}'
        )
        server = start_server()
        try:
            port = server.server_address[1]
            recommendation.endpoint = "http://127.0.0.1:" + str(port) + "/v1/recommendation"
            out = io.StringIO()
            code = recommendation.run_recommendation_search(
                ["--course", "高等数学", "--teacher", "张", "--top", "5"],
                out,
            )
        finally:
            stop_server(server)
        self.assertEqual(code, 0)
        parsed = urlparse(SERVER_STATE["paths"][0])
        query = parse_qs(parsed.query)
        self.assertEqual(parsed.path, "/v1/recommendation")
        self.assertEqual(query.get("course"), ["高等数学"])
        self.assertEqual(query.get("teacher"), ["张"])
        self.assertEqual(query.get("top"), ["5"])
        self.assertEqual(SERVER_STATE["auth"], "")
        self.assertEqual(SERVER_STATE["cookie"], "")
        body = json.loads(out.getvalue())
        self.assertTrue(body["ok"])
        self.assertEqual(body["source"], "recommendation")
        self.assertEqual(body["count"], 1)
        self.assertEqual(len(body["items"]), 1)
        self.assertEqual(events, ["search:success"])

    def test_cli_dispatch_defaults_top(self):
        events = capture_usage()
        SERVER_STATE["body"] = (
            '{"code":"OK","data":{"count":0,"items":[],"updated_at":"2026-09-08T00:00:00Z","version":"local"}}'
        )
        server = start_server()
        try:
            port = server.server_address[1]
            recommendation.endpoint = "http://127.0.0.1:" + str(port) + "/v1/recommendation"
            out = io.StringIO()
            code = run(["recommendation", "search", "--teacher", "王"], out, io.StringIO())
        finally:
            stop_server(server)
        self.assertEqual(code, 0)
        parsed = urlparse(SERVER_STATE["paths"][0])
        query = parse_qs(parsed.query)
        self.assertEqual(query.get("teacher"), ["王"])
        self.assertEqual(query.get("top"), ["20"])
        self.assertIsNone(query.get("course"))
        self.assertEqual(events, ["search:success"])

        out = io.StringIO()
        run(["recommendations", "search", "--teacher", "王"], out, io.StringIO())
        body = json.loads(out.getvalue())
        self.assertFalse(body["ok"])
        self.assertIn("unknown command: recommendations", body["error"])

    def test_rejects_missing_conditions_and_remote_failure(self):
        events = capture_usage()
        out = io.StringIO()
        code = recommendation.run_recommendation_search(["--course", " "], out)
        self.assertNotEqual(code, 0)
        self.assertIn("至少提供一个非空", out.getvalue())
        self.assertEqual(events, [])

        out = io.StringIO()
        code = recommendation.run_recommendation_search(["--course", "高数", "--top", "101"], out)
        self.assertNotEqual(code, 0)
        self.assertIn("1 到 100", out.getvalue())
        self.assertEqual(events, [])

        SERVER_STATE["status"] = 400
        SERVER_STATE["body"] = '{"code":"INVALID_REQUEST","data":null,"message":"查询至少需要 course 或 teacher"}'
        server = start_server()
        try:
            port = server.server_address[1]
            recommendation.endpoint = "http://127.0.0.1:" + str(port)
            out = io.StringIO()
            code = recommendation.run_recommendation_search(["--course", "高数"], out)
        finally:
            stop_server(server)
        self.assertNotEqual(code, 0)
        self.assertIn("查询至少需要 course 或 teacher", out.getvalue())
        self.assertEqual(events, ["search:failure"])


if __name__ == "__main__":
    unittest.main()
