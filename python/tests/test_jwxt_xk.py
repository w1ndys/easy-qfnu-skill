import io
import json
import os
import tempfile
import unittest

from qfnu import telemetry
from qfnu.jwxt import run_jwxt
from qfnu.jwxt_client import JWXTClient
from qfnu.jwxt_xk import (
    XK_MODULES,
    parse_xk_command,
    parse_xk_courses,
    parse_xk_rounds,
    pick_xk_round,
    resolve_xk_modules,
    summarize_xk_modules,
)

ROUNDS_HTML = """
<table>
<tr><th>选课轮次名称</th><th>开始时间</th><th>结束时间</th><th>操作</th></tr>
<tr>
  <td>2025-2026-3公选课</td>
  <td>2026-03-01 08:00</td>
  <td>2026-03-07 18:00</td>
  <td><a href="#" onclick="xsxkFun('ABC123')">进入选课</a></td>
</tr>
</table>
<a id="jrxk" href="/jsxsd/xsxk/xsxk_index?jx0502zbid=ABC123">进入选课</a>
"""
COURSE_JSON = (
    '{"aaData":[{"kch":"1001","kcmc":"<span>音乐鉴赏</span>","skls":"王老师",'
    '"syrs":"<font>3</font>","xkrs":10,"pkrs":40,"sksj":"周一 1-2","skdd":"日照1教",'
    '"dwmc":"音乐学院","ktmc":"01班","ctsm":null}]}'
)


def write_session(temp):
    path = os.path.join(temp, "session.json")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write('{"cookies":[]}\n')
    return path


class SessionEnv:
    def __init__(self, path):
        self.path = path
        self.old_cookie = os.environ.get("QFNU_JWXT_COOKIE_PATH")
        self.old_session = os.environ.get("QFNU_JWXT_SESSION_PATH")

    def __enter__(self):
        os.environ["QFNU_JWXT_COOKIE_PATH"] = self.path
        os.environ.pop("QFNU_JWXT_SESSION_PATH", None)
        return self

    def __exit__(self, exc_type, exc, tb):
        del exc_type, exc, tb
        if self.old_cookie is None:
            os.environ.pop("QFNU_JWXT_COOKIE_PATH", None)
        else:
            os.environ["QFNU_JWXT_COOKIE_PATH"] = self.old_cookie
        if self.old_session is None:
            os.environ.pop("QFNU_JWXT_SESSION_PATH", None)
        else:
            os.environ["QFNU_JWXT_SESSION_PATH"] = self.old_session


def install_router(pages):
    original = JWXTClient.text

    def fake_text(self, method, target, body=None, headers=None, same_origin=False):
        del self, body, headers, same_origin
        if "xklc_list" in target:
            return pages.get("list", (200, target, ROUNDS_HTML))
        if "xsxk_index" in target:
            return pages.get("enter", (200, target, "ok"))
        if method == "POST" and "xsxkGgxxkxk" in target:
            return pages.get("ggxxkxk", (200, target, '{"aaData":[{"kch":"1001","kcmc":"音乐鉴赏","skls":"王","syrs":"2"}]}'))
        if method == "POST" and "/jsxsd/xsxkkc/" in target:
            return pages.get("empty", (200, target, '{"aaData":[]}'))
        raise AssertionError(method + " " + target)

    JWXTClient.text = fake_text
    return original


class JWXTXKParseTest(unittest.TestCase):
    def test_parse_xk_rounds_reads_ids_and_table_fields(self):
        rounds = parse_xk_rounds(ROUNDS_HTML)
        self.assertEqual(len(rounds), 1)
        self.assertEqual(rounds[0]["id"], "ABC123")
        self.assertEqual(rounds[0]["name"], "2025-2026-3公选课")
        self.assertEqual(rounds[0]["start"], "2026-03-01 08:00")
        self.assertEqual(rounds[0]["end"], "2026-03-07 18:00")

    def test_parse_xk_courses_strips_html_and_keeps_module(self):
        items = parse_xk_courses(COURSE_JSON, XK_MODULES[4])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["course_name"], "音乐鉴赏")
        self.assertEqual(items[0]["remaining"], "3")
        self.assertEqual(items[0]["module"], "ggxxkxk")
        self.assertEqual(items[0]["module_name"], "公选课选课")
        self.assertNotIn("jx0404id", items[0])

    def test_parse_xk_courses_omits_selection_ids(self):
        raw = '{"aaData":[{"kch":"1","kcmc":"课","jx0404id":"secret","jx02id":"also"}]}'
        items = parse_xk_courses(raw, XK_MODULES[0])
        encoded = json.dumps(items[0], ensure_ascii=False)
        self.assertNotIn("secret", encoded)
        self.assertNotIn("jx0404", encoded)

    def test_summarize_xk_modules_reports_where_course_lives(self):
        items = [
            {"module": "ggxxkxk", "module_name": "公选课选课"},
            {"module": "ggxxkxk", "module_name": "公选课选课"},
            {"module": "xxxk", "module_name": "选修选课"},
        ]
        located = summarize_xk_modules(items)
        self.assertEqual(located[0], {"key": "ggxxkxk", "name": "公选课选课", "count": 2})
        self.assertEqual(located[1], {"key": "xxxk", "name": "选修选课", "count": 1})

    def test_resolve_xk_modules_accepts_chinese_aliases(self):
        modules = resolve_xk_modules(["公选课", "选修"])
        self.assertEqual([item["key"] for item in modules], ["ggxxkxk", "xxxk"])

    def test_pick_xk_round_requires_id_when_multiple(self):
        rounds = [{"id": "A"}, {"id": "B"}]
        with self.assertRaises(Exception) as caught:
            pick_xk_round(rounds, "")
        self.assertIn("--round", str(caught.exception))
        self.assertEqual(pick_xk_round(rounds, "B")["id"], "B")

    def test_parse_xk_command_rejects_bad_limit(self):
        with self.assertRaises(ValueError):
            parse_xk_command("search", ["--limit", "high"])
        with self.assertRaises(ValueError):
            parse_xk_command("search", ["--limit", "0"])


class JWXTXKCLITest(unittest.TestCase):
    def test_cli_xk_rounds_returns_live_notice(self):
        events = []
        original_usage = telemetry.report_usage
        telemetry.report_usage = lambda feature, status: events.append((feature, status))
        original = install_router({})
        try:
            with tempfile.TemporaryDirectory() as temp, SessionEnv(write_session(temp)):
                out = io.StringIO()
                code = run_jwxt(["xk", "rounds"], out)
        finally:
            JWXTClient.text = original
            telemetry.report_usage = original_usage
        self.assertEqual(code, 0)
        body = json.loads(out.getvalue())
        self.assertTrue(body["ok"])
        self.assertEqual(body["query_kind"], "live")
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["rounds"][0]["id"], "ABC123")
        self.assertEqual(events, [("jwxt.xk.rounds", "success")])

    def test_cli_xk_search_locates_course_module(self):
        original = install_router({})
        try:
            with tempfile.TemporaryDirectory() as temp, SessionEnv(write_session(temp)):
                out = io.StringIO()
                code = run_jwxt(["xk", "search", "--course", "音乐鉴赏", "--limit", "20"], out)
        finally:
            JWXTClient.text = original
        self.assertEqual(code, 0)
        body = json.loads(out.getvalue())
        self.assertEqual(body["query_kind"], "live")
        self.assertEqual(body["located_modules"][0]["key"], "ggxxkxk")
        self.assertEqual(body["items"][0]["course_name"], "音乐鉴赏")
        self.assertEqual(body["scanned_modules"][-1], "ggxxkxk")

    def test_cli_xk_search_no_open_round(self):
        original = install_router({"list": (200, "http://zhjw.qfnu.edu.cn/jsxsd/xsxk/xklc_list", "<html>暂无</html>")})
        try:
            with tempfile.TemporaryDirectory() as temp, SessionEnv(write_session(temp)):
                out = io.StringIO()
                run_jwxt(["xk", "search", "--course", "音乐鉴赏"], out)
        finally:
            JWXTClient.text = original
        body = json.loads(out.getvalue())
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "当前没有开放的选课轮次")
        self.assertIn("precourse search", body["hint"])

    def test_cli_xk_search_requires_login(self):
        original = install_router(
            {"list": (200, "http://zhjw.qfnu.edu.cn/jsxsd/xsxk/xklc_list", "请输入账号 请输入密码 请输入验证码")}
        )
        try:
            with tempfile.TemporaryDirectory() as temp, SessionEnv(write_session(temp)):
                out = io.StringIO()
                run_jwxt(["xk", "rounds"], out)
        finally:
            JWXTClient.text = original
        body = json.loads(out.getvalue())
        self.assertEqual(body["error"], "选课查询需要已登录的教务会话")

    def test_cli_xk_unknown_action(self):
        out = io.StringIO()
        code = run_jwxt(["xk", "select"], out)
        self.assertEqual(code, 0)
        body = json.loads(out.getvalue())
        self.assertIn("unknown xk action: select", body["error"])
