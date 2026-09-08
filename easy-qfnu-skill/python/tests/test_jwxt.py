import io
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from qfnu.cli import run
from qfnu.jwxt import run_jwxt
from qfnu.jwxt_auth import encode_credentials, login_failure_hint, parse_login_message
from qfnu.jwxt_auth import status as jwxt_status
from qfnu.jwxt_client import JWXT_BASE, JWXTClient, cookies_for_url, make_cookie


class JWXTAuthTest(unittest.TestCase):
    def test_encode_credentials_matches_qfnu_protocol(self):
        self.assertEqual(encode_credentials("abc", "pw", "XYZ123", "10120"), "aXbcY%Z1%%pw")

    def test_encode_credentials_keeps_characters_after_limit(self):
        username = "12345678901234567890"
        self.assertEqual(encode_credentials(username, "pw", "", ""), username[:20] + "%%%pw")

    def test_parse_login_message_returns_exact_show_msg_text(self):
        raw = (
            '<li class="input_li" id="showMsg" style="color: red; margin-bottom: 0;">'
            "\n\t\t\t&nbsp;验证码错误!!\n\t\t</li>"
        )
        self.assertEqual(parse_login_message(raw), "验证码错误!!")

    def test_parse_login_message_supports_nested_markup(self):
        raw = '<div id=\'showMsg\'><font color="red">用户名或密码错误</font></div>'
        self.assertEqual(parse_login_message(raw), "用户名或密码错误")

    def test_parse_login_message_ignores_page_without_show_msg(self):
        self.assertEqual(parse_login_message("<html><body>登录失败</body></html>"), "")

    def test_login_failure_hint_matches_captcha_message(self):
        self.assertEqual(
            login_failure_hint("验证码错误!!"),
            "重新运行 easy-qfnu jwxt captcha 获取新验证码",
        )

    def test_status_reports_corrupt_session(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "session.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{")
            out = io.StringIO()
            run_jwxt(["status", "--session-path", path], out)
            self.assertIn("parse JWXT session", out.getvalue())

    def test_logout_removes_corrupt_session(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "session.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{")
            out = io.StringIO()
            run_jwxt(["logout", "--session-path", path], out)
            self.assertFalse(os.path.exists(path))
            self.assertIn('"logged_in": false', out.getvalue())

    def test_logout_reports_removal_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "session")
            os.mkdir(path)
            with open(os.path.join(path, "child"), "w", encoding="utf-8") as handle:
                handle.write("x")
            out = io.StringIO()
            run_jwxt(["logout", "--session-path", path], out)
            self.assertIn("clear JWXT session", out.getvalue())

    def test_cli_dispatches_jwxt_and_rejects_unknown_action(self):
        out = io.StringIO()
        code = run(["jwxt", "grades"], out, io.StringIO())
        self.assertEqual(code, 0)
        body = json.loads(out.getvalue())
        self.assertFalse(body["ok"])
        self.assertIn("unknown action: grades", body["error"])

    def test_status_keeps_login_when_profile_enrichment_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            client = JWXTClient(os.path.join(temp, "session.json"), "")
            client.meta["username"] = "student"
            client.jar.set_cookie(
                make_cookie("JSESSIONID", "active", "/", "zhjw.qfnu.edu.cn", False, None)
            )

            def fake_request(method, target, body=None, headers=None, same_origin=False):
                del method, body, headers, same_origin
                if target.endswith("/jsxsd/framework/xsMain.jsp"):
                    return 200, target, "教学一体化服务平台".encode()
                raise OSError("profile unavailable")

            client.request = fake_request
            result = jwxt_status(client)
            self.assertTrue(result["logged_in"])
            self.assertIn("资料补全", result.get("profile_warning", ""))

    def test_session_persists_cookies_for_scoped_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "session.json")
            client = JWXTClient(path, "")
            client.jar.set_cookie(make_cookie("JSESSIONID", "root", "/", "zhjw.qfnu.edu.cn", False, None))
            client.jar.set_cookie(
                make_cookie("JSESSIONID", "jsxsd", "/jsxsd", "zhjw.qfnu.edu.cn", False, None)
            )
            client.persist({})
            with open(path, "r", encoding="utf-8") as handle:
                saved = json.load(handle)
            paths = {item["Path"]: item["Value"] for item in saved["cookies"]}
            self.assertEqual(paths.get("/"), "root")
            self.assertEqual(paths.get("/jsxsd"), "jsxsd")

            loaded = JWXTClient(path, "")
            loaded.load()
            root_values = cookie_values(cookies_for_url(loaded.jar, JWXT_BASE + "/"), "JSESSIONID")
            jsxsd_values = cookie_values(
                cookies_for_url(loaded.jar, JWXT_BASE + "/jsxsd/framework/xsMain.jsp"),
                "JSESSIONID",
            )
            self.assertEqual(root_values, ["root"])
            self.assertIn("jsxsd", jsxsd_values)


def cookie_values(cookies, name):
    values = []
    for cookie in cookies:
        if cookie.name == name:
            values.append(cookie.value)
    return values


class RedirectTest(unittest.TestCase):
    def test_request_follows_same_origin_redirect(self):
        server = start_redirect_server()
        try:
            origin = "http://127.0.0.1:" + str(server.server_address[1])
            client = JWXTClient()
            status, final_url, body = client.request_with_origin(
                "POST",
                origin + "/start",
                b"payload",
                None,
                origin,
            )
        finally:
            stop_server(server)
        self.assertEqual(status, 200)
        self.assertEqual(final_url, origin + "/finish")
        self.assertEqual(body, b"authenticated")

    def test_request_stops_cross_origin_redirect(self):
        hits = {"count": 0}

        class DestHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                hits["count"] += 1
                payload = b"must not follow"
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args):
                return

        dest = HTTPServer(("127.0.0.1", 0), DestHandler)
        dest_thread = threading.Thread(target=dest.serve_forever, daemon=True)
        dest_thread.start()
        dest_url = "http://127.0.0.1:" + str(dest.server_address[1])

        class SourceHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", dest_url + "/finish")
                self.end_headers()
                body = ('<a href="' + dest_url + '/finish">Found</a>').encode("utf-8")
                self.wfile.write(body)

            def log_message(self, *_args):
                return

        source = HTTPServer(("127.0.0.1", 0), SourceHandler)
        source_thread = threading.Thread(target=source.serve_forever, daemon=True)
        source_thread.start()
        try:
            origin = "http://127.0.0.1:" + str(source.server_address[1])
            client = JWXTClient()
            status, final_url, body = client.request_with_origin(
                "GET",
                origin + "/start",
                None,
                None,
                origin,
            )
        finally:
            stop_server(source)
            stop_server(dest)
        self.assertEqual(status, 302)
        self.assertEqual(final_url, origin + "/start")
        self.assertEqual(hits["count"], 0)
        self.assertIn(dest_url, body.decode("utf-8", errors="replace"))


def start_redirect_server():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or "0")
            self.rfile.read(length)
            if self.path == "/start":
                self.send_response(302)
                self.send_header("Location", "/finish")
                self.end_headers()
                return
            self.send_response(404)
            self.end_headers()

        def do_GET(self):
            if self.path == "/finish":
                payload = b"authenticated"
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, *_args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def stop_server(server):
    server.shutdown()
    server.server_close()


if __name__ == "__main__":
    unittest.main()
