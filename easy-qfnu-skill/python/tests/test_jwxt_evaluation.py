import io
import json
import os
import tempfile
import unittest

from qfnu import telemetry
from qfnu.jwxt import run_jwxt
from qfnu.jwxt_client import JWXTClient
from qfnu.jwxt_evaluation import (
    evaluation_preset,
    evaluation_rows,
    parse_evaluation_detail,
)

FIND_HTML = '<a href="/jsxsd/xspj/xspj_list.do?pj01id=BATCH">进入评价</a>'
LIST_HTML = """
<table id="dataList">
<tr><th>课程名称</th><th>授课教师</th><th>是否提交</th><th>操作</th></tr>
<tr>
  <td>高数</td>
  <td>李老师</td>
  <td>否</td>
  <td><a href="/jsxsd/xspj/xspj_edit.do?id=a">评价</a></td>
</tr>
<tr>
  <td>英语</td>
  <td>王老师</td>
  <td>是</td>
  <td></td>
</tr>
</table>
"""
PENDING_LIST_HTML = """
<table id="dataList">
<tr><th>课程名称</th><th>授课教师</th><th>是否提交</th><th>操作</th></tr>
<tr>
  <td>高数</td>
  <td>李老师</td>
  <td>否</td>
  <td><a href="/jsxsd/xspj/xspj_edit.do?id=a">评价</a></td>
</tr>
<tr>
  <td>英语</td>
  <td>王老师</td>
  <td>否</td>
  <td><a href="/jsxsd/xspj/xspj_edit.do?id=b">评价</a></td>
</tr>
</table>
"""
EDIT_HTML = """
<form id="Form1">
<input type="hidden" name="pj01id" value="BATCH">
<table>
<tr>
  <td>教学内容</td>
  <td>10</td>
  <td>
    <input type="hidden" name="pj06xh" value="ind1">
    <input type="radio" name="pj0601id_ind1" value="optA">
    <input type="radio" name="pj0601id_ind1" value="optB">
  </td>
</tr>
<tr>
  <td>教学态度</td>
  <td>5</td>
  <td>
    <input type="hidden" name="pj06xh" value="ind2">
    <input type="radio" name="pj0601id_ind2" value="optC">
  </td>
</tr>
</table>
</form>
"""


def write_session(temp):
    path = os.path.join(temp, "session.json")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write('{"cookies":[]}\n')
    return path


def install_router(pages, posts=None):
    original = JWXTClient.text

    def fake_text(self, method, target, body=None, headers=None, same_origin=False):
        del self, headers, same_origin
        if method == "POST":
            if posts is not None:
                posts.append((target, body))
            return pages.get("POST:" + target, pages.get("POST", (200, target, "提交成功")))
        for key, value in pages.items():
            if key.startswith("POST"):
                continue
            if key in target:
                return value
        raise AssertionError(method + " " + target)

    JWXTClient.text = fake_text
    return original


class JWXTEvaluationParseTest(unittest.TestCase):
    def test_evaluation_rows_maps_status_and_href(self):
        items = evaluation_rows(LIST_HTML)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["id"], "0")
        self.assertEqual(items[0]["course_name"], "高数")
        self.assertEqual(items[0]["teacher_name"], "李老师")
        self.assertEqual(items[0]["status"], "未评")
        self.assertIn("xspj_edit.do", items[0]["href"])
        self.assertEqual(items[1]["status"], "已提交")

    def test_parse_evaluation_detail_reads_indicators(self):
        detail = parse_evaluation_detail(EDIT_HTML, {"href": "http://zhjw.qfnu.edu.cn/jsxsd/xspj/xspj_edit.do"})
        self.assertEqual(detail["ids"], ["ind1", "ind2"])
        self.assertEqual(detail["static"]["pj01id"], "BATCH")
        self.assertEqual(detail["options"]["ind1"][0]["score"], 10.0)
        self.assertEqual(detail["options"]["ind2"][0]["score"], 5.0)

    def test_evaluation_preset_rejects_invalid_option_score(self):
        detail = {
            "ids": ["indicator"],
            "options": {"indicator": [{"option_id": "good", "score": "invalid"}]},
        }
        with self.assertRaises(Exception) as caught:
            evaluation_preset(detail, 89)
        self.assertIn("invalid score", str(caught.exception))


class JWXTEvaluationCLITest(unittest.TestCase):
    def test_cli_evaluations_returns_items_and_reports_usage(self):
        events = []
        original_usage = telemetry.report_usage
        telemetry.report_usage = lambda feature, status: events.append((feature, status))
        original = install_router(
            {
                "xspj_find.do": (200, "http://zhjw.qfnu.edu.cn/jsxsd/xspj/xspj_find.do", FIND_HTML),
                "xspj_list.do": (200, "http://zhjw.qfnu.edu.cn/jsxsd/xspj/xspj_list.do", LIST_HTML),
            }
        )
        try:
            with tempfile.TemporaryDirectory() as temp:
                out = io.StringIO()
                code = run_jwxt(["evaluations", "--session-path", write_session(temp)], out)
        finally:
            JWXTClient.text = original
            telemetry.report_usage = original_usage
        self.assertEqual(code, 0)
        body = json.loads(out.getvalue())
        self.assertTrue(body["ok"])
        self.assertEqual(body["count"], 2)
        self.assertEqual(body["items"], body["evaluations"])
        self.assertEqual(body["items"][0]["course_name"], "高数")
        self.assertEqual(events, [("jwxt.evaluations", "success")])

    def test_evaluations_requires_login_page(self):
        events = []
        original_usage = telemetry.report_usage
        telemetry.report_usage = lambda feature, status: events.append((feature, status))
        original = install_router(
            {"xspj_find.do": (200, "http://zhjw.qfnu.edu.cn/jsxsd/xspj/xspj_find.do", "请输入账号 请输入密码 请输入验证码")}
        )
        try:
            with tempfile.TemporaryDirectory() as temp:
                out = io.StringIO()
                code = run_jwxt(["evaluations", "--session-path", write_session(temp)], out)
        finally:
            JWXTClient.text = original
            telemetry.report_usage = original_usage
        self.assertEqual(code, 0)
        body = json.loads(out.getvalue())
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "evaluation page requires login")
        self.assertEqual(events, [("jwxt.evaluations", "failure")])

    def test_evaluations_without_batch(self):
        original = install_router(
            {"xspj_find.do": (200, "http://zhjw.qfnu.edu.cn/jsxsd/xspj/xspj_find.do", "<html>暂无评价</html>")}
        )
        try:
            with tempfile.TemporaryDirectory() as temp:
                out = io.StringIO()
                run_jwxt(["evaluations", "--session-path", write_session(temp)], out)
        finally:
            JWXTClient.text = original
        body = json.loads(out.getvalue())
        self.assertEqual(body["error"], "no active evaluation batch")

    def test_cli_evaluate_is_dry_run_without_confirm(self):
        posts = []
        events = []
        original_usage = telemetry.report_usage
        telemetry.report_usage = lambda feature, status: events.append((feature, status))
        original = install_router(
            {
                "xspj_find.do": (200, "http://zhjw.qfnu.edu.cn/jsxsd/xspj/xspj_find.do", FIND_HTML),
                "xspj_list.do": (200, "http://zhjw.qfnu.edu.cn/jsxsd/xspj/xspj_list.do", LIST_HTML),
                "xspj_edit.do": (200, "http://zhjw.qfnu.edu.cn/jsxsd/xspj/xspj_edit.do", EDIT_HTML),
            },
            posts,
        )
        try:
            with tempfile.TemporaryDirectory() as temp:
                out = io.StringIO()
                code = run_jwxt(["evaluate", "--session-path", write_session(temp)], out)
        finally:
            JWXTClient.text = original
            telemetry.report_usage = original_usage
        self.assertEqual(code, 0)
        body = json.loads(out.getvalue())
        self.assertTrue(body["ok"])
        self.assertTrue(body["dry_run"])
        self.assertEqual(body["target_score"], 89)
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["items"][0]["course_name"], "高数")
        self.assertEqual(body["items"][0]["total_score"], 15.0)
        self.assertEqual(posts, [])
        self.assertEqual(events, [("jwxt.evaluate", "success")])

    def test_cli_evaluate_confirm_posts_once(self):
        posts = []
        original = install_router(
            {
                "xspj_find.do": (200, "http://zhjw.qfnu.edu.cn/jsxsd/xspj/xspj_find.do", FIND_HTML),
                "xspj_list.do": (200, "http://zhjw.qfnu.edu.cn/jsxsd/xspj/xspj_list.do", LIST_HTML),
                "xspj_edit.do": (200, "http://zhjw.qfnu.edu.cn/jsxsd/xspj/xspj_edit.do", EDIT_HTML),
                "POST": (200, "http://zhjw.qfnu.edu.cn/jsxsd/xspj/xspj_save.do", "提交成功"),
            },
            posts,
        )
        try:
            with tempfile.TemporaryDirectory() as temp:
                out = io.StringIO()
                code = run_jwxt(
                    ["evaluate", "--score", "89", "--course", "0", "--confirm", "--session-path", write_session(temp)],
                    out,
                )
        finally:
            JWXTClient.text = original
        self.assertEqual(code, 0)
        body = json.loads(out.getvalue())
        self.assertTrue(body["ok"])
        self.assertFalse(body["dry_run"])
        self.assertEqual(body["submitted"], 1)
        self.assertEqual(len(posts), 1)
        self.assertIn(b"issubmit=1", posts[0][1])
        self.assertIn(b"pj06xh=ind1", posts[0][1])

    def test_cli_evaluate_stops_after_first_failed_submit(self):
        posts = []
        original = install_router(
            {
                "xspj_find.do": (200, "http://zhjw.qfnu.edu.cn/jsxsd/xspj/xspj_find.do", FIND_HTML),
                "xspj_list.do": (200, "http://zhjw.qfnu.edu.cn/jsxsd/xspj/xspj_list.do", PENDING_LIST_HTML),
                "xspj_edit.do": (200, "http://zhjw.qfnu.edu.cn/jsxsd/xspj/xspj_edit.do", EDIT_HTML),
                "POST": (200, "http://zhjw.qfnu.edu.cn/jsxsd/xspj/xspj_save.do", "系统繁忙"),
            },
            posts,
        )
        try:
            with tempfile.TemporaryDirectory() as temp:
                out = io.StringIO()
                run_jwxt(["evaluate", "--confirm", "--session-path", write_session(temp)], out)
        finally:
            JWXTClient.text = original
        body = json.loads(out.getvalue())
        self.assertFalse(body["ok"])
        self.assertEqual(body["failed"], 1)
        self.assertEqual(body["skipped"], 1)
        self.assertTrue(body["results"][1]["skipped"])
        self.assertEqual(len(posts), 1)

    def test_cli_evaluate_rejects_non_numeric_score(self):
        out = io.StringIO()
        code = run_jwxt(["evaluate", "--score", "high"], out)
        self.assertEqual(code, 0)
        body = json.loads(out.getvalue())
        self.assertFalse(body["ok"])
        self.assertIn("--score must be an integer", body["error"])

    def test_cli_evaluate_rejects_score_out_of_range(self):
        with tempfile.TemporaryDirectory() as temp:
            out = io.StringIO()
            run_jwxt(["evaluate", "--score", "101", "--session-path", write_session(temp)], out)
        body = json.loads(out.getvalue())
        self.assertEqual(body["error"], "target score must be between 0 and 100")

    def test_cli_evaluate_warns_for_score_100(self):
        original = install_router(
            {
                "xspj_find.do": (200, "http://zhjw.qfnu.edu.cn/jsxsd/xspj/xspj_find.do", FIND_HTML),
                "xspj_list.do": (200, "http://zhjw.qfnu.edu.cn/jsxsd/xspj/xspj_list.do", LIST_HTML),
                "xspj_edit.do": (200, "http://zhjw.qfnu.edu.cn/jsxsd/xspj/xspj_edit.do", EDIT_HTML),
            }
        )
        try:
            with tempfile.TemporaryDirectory() as temp:
                out = io.StringIO()
                run_jwxt(["evaluate", "--score", "100", "--session-path", write_session(temp)], out)
        finally:
            JWXTClient.text = original
        body = json.loads(out.getvalue())
        self.assertIn("98", body["warning"])
