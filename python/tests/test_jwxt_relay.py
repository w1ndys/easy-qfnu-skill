import io
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

from qfnu import jwxt_relay, telemetry
from qfnu.jwxt import run_jwxt
from qfnu.jwxt_client import JWXTClient, make_cookie


def start_capture_server(payload, status=200):
    captured = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.capture()

        def do_POST(self):
            self.capture()

        def capture(self):
            length = int(self.headers.get("Content-Length") or "0")
            path = self.path
            query = ""
            if "?" in path:
                path, query = path.split("?", 1)
            captured["method"] = self.command
            captured["path"] = path
            captured["query"] = query
            captured["body"] = self.rfile.read(length) if length else b""
            captured["cookie"] = self.headers.get("X-QFNU-JWXT-Cookie") or ""
            captured["key"] = self.headers.get("Idempotency-Key") or ""
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, captured


def stop_server(server):
    server.shutdown()
    server.server_close()


def relay_client():
    client = JWXTClient()
    client.jar.set_cookie(make_cookie("JSESSIONID", "abc", "/", "zhjw.qfnu.edu.cn", False, None))
    client.jar.set_cookie(make_cookie("route", "blue", "/", "zhjw.qfnu.edu.cn", False, None))
    return client


class JWXTRelayTest(unittest.TestCase):
    def test_feedback_sends_json_and_session_cookie(self):
        payload = b'{"code":"OK","data":{"submitted":true}}'
        server, captured = start_capture_server(payload)
        old = dict(jwxt_relay.RELAY_TARGETS["feedback"])
        jwxt_relay.RELAY_TARGETS["feedback"] = {
            "endpoint": "http://127.0.0.1:" + str(server.server_address[1]),
            "method": "POST",
            "sends_cookie": True,
            "sends_idempotency": True,
        }
        events = []
        original = telemetry.report_usage
        telemetry.report_usage = lambda feature, status: events.append((feature, status))
        try:
            out = io.StringIO()
            body = '{"category_id":"bug","text":"页面打不开"}'
            code = jwxt_relay.run_jwxt_relay("feedback", relay_client(), io.StringIO(body), out)
        finally:
            telemetry.report_usage = original
            jwxt_relay.RELAY_TARGETS["feedback"] = old
            stop_server(server)
        self.assertEqual(code, 0)
        self.assertEqual(captured["body"].decode("utf-8"), body)
        self.assertEqual(captured["cookie"], "JSESSIONID=abc; route=blue")
        self.assertEqual(captured["key"], jwxt_relay.relay_idempotency_key(body.encode("utf-8")))
        response = json.loads(out.getvalue())
        self.assertEqual(response["code"], "OK")
        self.assertEqual(events, [("relay.feedback", "success")])

    def test_recommendation_posts_confirmed_json(self):
        payload = b'{"code":"OK","data":{"submitted":true}}'
        server, captured = start_capture_server(payload)
        old = dict(jwxt_relay.RELAY_TARGETS["recommendation"])
        jwxt_relay.RELAY_TARGETS["recommendation"] = {
            "endpoint": "http://127.0.0.1:" + str(server.server_address[1]),
            "method": "POST",
            "sends_cookie": True,
            "sends_idempotency": True,
        }
        events = []
        original = telemetry.report_usage
        telemetry.report_usage = lambda feature, status: events.append((feature, status))
        try:
            out = io.StringIO()
            body = '{"course_name":"高数","teacher_name":"张三","year":"2024-2025","reason":"讲得清楚","nickname":null}'
            code = jwxt_relay.run_jwxt_relay(
                "recommendation",
                relay_client(),
                io.StringIO(body),
                out,
            )
        finally:
            telemetry.report_usage = original
            jwxt_relay.RELAY_TARGETS["recommendation"] = old
            stop_server(server)
        self.assertEqual(code, 0)
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["body"].decode("utf-8"), body)
        self.assertEqual(captured["cookie"], "JSESSIONID=abc; route=blue")
        self.assertEqual(captured["key"], jwxt_relay.relay_idempotency_key(body.encode("utf-8")))
        self.assertEqual(json.loads(out.getvalue())["code"], "OK")
        self.assertEqual(events, [("relay.recommendation", "success")])

    def test_rank_uses_get_and_query_parameters(self):
        payload = b'{"code":"OK","data":{"class_rank":1}}'
        server, captured = start_capture_server(payload)
        old = dict(jwxt_relay.RELAY_TARGETS["rank"])
        jwxt_relay.RELAY_TARGETS["rank"] = {
            "endpoint": "http://127.0.0.1:" + str(server.server_address[1]) + "/v1/ranking/me",
            "method": "GET",
            "sends_cookie": True,
            "sends_idempotency": False,
        }
        try:
            out = io.StringIO()
            body = '{"scope":"both","course_codes":["CS101","CS102"]}'
            code = jwxt_relay.run_jwxt_relay("rank", relay_client(), io.StringIO(body), out)
        finally:
            jwxt_relay.RELAY_TARGETS["rank"] = old
            stop_server(server)
        self.assertEqual(code, 0)
        self.assertEqual(captured["method"], "GET")
        self.assertEqual(captured["path"], "/v1/ranking/me")
        self.assertEqual(captured["body"], b"")
        values = parse_qs(captured["query"])
        self.assertEqual(values.get("scope"), ["both"])
        self.assertEqual(values.get("course_code"), ["CS101", "CS102"])
        self.assertEqual(captured["key"], "")

    def test_rejects_missing_session_and_custom_action(self):
        client = JWXTClient()
        out = io.StringIO()
        code = jwxt_relay.run_jwxt_relay("feedback", client, io.StringIO("{}"), out)
        self.assertNotEqual(code, 0)
        self.assertIn("no active JWXT session", out.getvalue())

        out = io.StringIO()
        code = jwxt_relay.run_jwxt_relay("custom", client, io.StringIO("{}"), out)
        self.assertNotEqual(code, 0)
        self.assertIn("unknown relay action", out.getvalue())

    def test_propagates_remote_http_failure_as_nonzero(self):
        payload = b'{"code":"INVALID_REQUEST","data":null}'
        server, _captured = start_capture_server(payload, 400)
        old = dict(jwxt_relay.RELAY_TARGETS["rank"])
        jwxt_relay.RELAY_TARGETS["rank"] = {
            "endpoint": "http://127.0.0.1:" + str(server.server_address[1]),
            "sends_cookie": True,
            "sends_idempotency": False,
        }
        events = []
        original = telemetry.report_usage
        telemetry.report_usage = lambda feature, status: events.append((feature, status))
        try:
            out = io.StringIO()
            code = jwxt_relay.run_jwxt_relay("rank", relay_client(), io.StringIO('{"scope":"both"}'), out)
        finally:
            telemetry.report_usage = original
            jwxt_relay.RELAY_TARGETS["rank"] = old
            stop_server(server)
        self.assertNotEqual(code, 0)
        self.assertIn('"INVALID_REQUEST"', out.getvalue())
        self.assertEqual(events, [("relay.rank", "failure")])

    def test_rejects_invalid_json(self):
        out = io.StringIO()
        events = []
        original = telemetry.report_usage
        telemetry.report_usage = lambda feature, status: events.append((feature, status))
        try:
            code = jwxt_relay.run_jwxt_relay("feedback", relay_client(), io.StringIO("{"), out)
        finally:
            telemetry.report_usage = original
        self.assertNotEqual(code, 0)
        self.assertIn("valid JSON", out.getvalue())
        self.assertEqual(events, [])

    def test_rank_rejects_unknown_fields_and_empty_codes(self):
        out = io.StringIO()
        code = jwxt_relay.run_jwxt_relay(
            "rank",
            relay_client(),
            io.StringIO('{"scope":"both","extra":1}'),
            out,
        )
        self.assertNotEqual(code, 0)
        self.assertIn("rank input must be a JSON object", out.getvalue())

        out = io.StringIO()
        code = jwxt_relay.run_jwxt_relay(
            "rank",
            relay_client(),
            io.StringIO('{"scope":"both","course_codes":[""]}'),
            out,
        )
        self.assertNotEqual(code, 0)
        self.assertIn("course_codes cannot contain empty values", out.getvalue())

    def test_cli_requires_one_fixed_action(self):
        out = io.StringIO()
        code = run_jwxt(["relay"], out, io.StringIO("{}"))
        self.assertEqual(code, 0)
        body = json.loads(out.getvalue())
        self.assertFalse(body["ok"])
        self.assertIn("relay requires one fixed action", body["error"])

    def test_cli_dispatches_unknown_relay_action(self):
        out = io.StringIO()
        code = run_jwxt(["relay", "custom"], out, io.StringIO("{}"))
        self.assertEqual(code, 1)
        body = json.loads(out.getvalue())
        self.assertFalse(body["ok"])
        self.assertIn("unknown relay action", body["error"])
