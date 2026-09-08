import json
import threading
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from qfnu import telemetry


class Recorder:
    def __init__(self):
        self.method = ""
        self.path = ""
        self.headers = {}
        self.body = b""


def start_server(recorder):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            recorder.method = self.command
            recorder.path = self.path
            recorder.headers = self.headers
            recorder.body = self.rfile.read(length)
            self.send_response(204)
            self.end_headers()

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


class TelemetryTest(unittest.TestCase):
    def test_send_only_anonymous_fields(self):
        old_version = telemetry.VERSION
        telemetry.VERSION = "v2026.09.01.1200"
        recorder = Recorder()
        server = start_server(recorder)
        try:
            client = telemetry.TelemetryClient(
                f"http://127.0.0.1:{server.server_address[1]}/v1/telemetry/events",
                lambda: datetime(2026, 9, 1, 4, 5, 6, tzinfo=timezone.utc),
            )
            client.send("jwxt.login", "success")
        finally:
            server.shutdown()
            server.server_close()
            telemetry.VERSION = old_version

        self.assertEqual(recorder.headers.get("Cookie"), None)
        self.assertEqual(recorder.headers.get("X-QFNU-JWXT-Cookie"), None)
        self.assertEqual(recorder.headers.get("Authorization"), None)
        event = json.loads(recorder.body.decode("utf-8"))
        self.assertEqual(event["feature"], "jwxt.login")
        self.assertEqual(event["status"], "success")
        self.assertEqual(event["cli_version"], "v2026.09.01.1200")
        self.assertEqual(event["os"], telemetry.current_os())
        self.assertEqual(event["arch"], telemetry.current_arch())
        self.assertEqual(event["occurred_at"], "2026-09-01T04:05:06Z")

    def test_login_success_keeps_failure_silent(self):
        old_reporter = telemetry.report_anonymous_event
        seen = []

        def boom(feature, status):
            seen.append(feature + ":" + status)
            raise OSError("service unavailable")

        telemetry.report_anonymous_event = boom
        result = {}
        try:
            telemetry.report_login_success(result)
        finally:
            telemetry.report_anonymous_event = old_reporter

        self.assertEqual(seen, ["jwxt.login:success"])
        notice = result["telemetry_notice"]
        self.assertIn("不含学号、姓名、Cookie", notice)
        self.assertNotIn("失败", notice)
        self.assertNotIn("unavailable", notice)

    def test_usage_status_maps_error(self):
        self.assertEqual(telemetry.usage_status(None), "success")
        self.assertEqual(telemetry.usage_status(ValueError("boom")), "failure")


if __name__ == "__main__":
    unittest.main()
