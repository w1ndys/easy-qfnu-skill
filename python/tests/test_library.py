import io
import json
import threading
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

from qfnu import library
from qfnu.cli import run

ORIGINAL_BASE = library.base

QS_BODY = (
    '{"code": 0, "data": {"date": ["2026-10-02", "2026-10-03"],'
    ' "premises": [{"id": "1", "name": "曲阜校区", "parentId": 0, "topId": "1", "total_num": 733, "free_num": 721}],'
    ' "storey": [{"id": "2", "name": "三楼", "parentId": "1", "topId": "1", "total_num": "300", "free_num": "257"}],'
    ' "area": [{"id": "12", "name": "二层自习室", "parentId": "2", "topId": "2", "total_num": "177", "free_num": "177"},'
    ' {"id": "13", "name": "四层东区自习室", "parentId": "3", "topId": "3", "total_num": 240, "free_num": 240}]},'
    ' "msg": "成功"}'
)

DATE_BODY = (
    '{"code": 1, "msg": "操作成功", "data": [{"day": "2026-10-02", "times": [[]]},'
    ' {"day": "2026-10-03", "times": [{"id": "1864001", "status": 1, "start": "08:00", "end": "22:00"}]}]}'
)

SEAT_BODY = (
    '{"code": 1, "msg": "操作成功", "data": ['
    '{"id": "3063", "no": "001", "name": "001", "area": "12", "status": "1", "status_name": "空闲",'
    ' "area_name": "二层自习室", "point_x": "27.5", "in_label": 1},'
    '{"id": "3064", "no": "002", "name": "002", "area": "12", "status": "2", "status_name": "已预约",'
    ' "area_name": "二层自习室"},'
    '{"id": "3065", "no": "003", "name": "003", "area": "12", "status": "6", "status_name": "使用中",'
    ' "area_name": "二层自习室"}]}'
)

SERVER_STATE = {
    "responses": {},
    "requests": [],
    "headers": [],
    "status": 200,
}


class LibraryHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        SERVER_STATE["requests"].append({"path": self.path, "body": json.loads(raw or "{}")})
        SERVER_STATE["headers"].append(dict(self.headers.items()))
        payload = SERVER_STATE["responses"].get(self.path, "{}").encode("utf-8")
        self.send_response(SERVER_STATE["status"])
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        return


def start_server():
    server = HTTPServer(("127.0.0.1", 0), LibraryHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def stop_server(server):
    server.shutdown()
    server.server_close()


class LibraryServerTest(unittest.TestCase):
    """共用本地假上游的基类：负责起服务、指向 library.base、清理请求记录。"""

    def setUp(self):
        SERVER_STATE["responses"] = {library.QUICK_SELECT_PATH: QS_BODY}
        SERVER_STATE["requests"] = []
        SERVER_STATE["headers"] = []
        SERVER_STATE["status"] = 200
        self.server = start_server()
        library.base = "http://127.0.0.1:" + str(self.server.server_address[1])

    def tearDown(self):
        stop_server(self.server)
        library.base = ORIGINAL_BASE

    def answer(self, path, body):
        SERVER_STATE["responses"][path] = body

    def paths(self):
        return [item["path"] for item in SERVER_STATE["requests"]]

    def payload(self, index=-1):
        return SERVER_STATE["requests"][index]["body"]


class LibraryAreasTest(LibraryServerTest):
    def test_areas_uses_no_login_and_normalizes_counts(self):
        out = io.StringIO()
        code = run(["library", "areas", "--date", "2026-10-03"], out, io.StringIO())
        self.assertEqual(code, 0)
        self.assertEqual(self.paths(), [library.QUICK_SELECT_PATH])
        self.assertEqual(
            self.payload(),
            {"id": "1", "date": "2026-10-03", "categoryIds": ["1"], "members": 0, "authorization": ""},
        )
        self.assertNotIn("Authorization", SERVER_STATE["headers"][0])
        self.assertNotIn("Cookie", SERVER_STATE["headers"][0])
        body = json.loads(out.getvalue())
        self.assertTrue(body["ok"])
        self.assertEqual(body["source"], "library")
        self.assertEqual(body["operation"], "areas")
        self.assertEqual(body["dates"], ["2026-10-02", "2026-10-03"])
        self.assertEqual(body["area_count"], 2)
        self.assertEqual(body["areas"][0]["free_num"], 177)
        self.assertEqual(body["areas"][0]["total_num"], 177)
        self.assertEqual(body["areas"][1]["free_num"], 240)
        self.assertEqual(body["floors"][0]["total_num"], 300)
        self.assertEqual(body["campuses"][0]["name"], "曲阜校区")
        self.assertEqual(body["free_num"], 417)

    def test_areas_defaults_to_empty_date_and_surfaces_upstream_message(self):
        out = io.StringIO()
        run(["library", "areas"], out, io.StringIO())
        self.assertEqual(self.payload()["date"], "")

        self.answer(library.QUICK_SELECT_PATH, '{"code": -1, "msg": "系统维护中", "data": null}')
        out = io.StringIO()
        code = run(["library", "areas"], out, io.StringIO())
        self.assertNotEqual(code, 0)
        body = json.loads(out.getvalue())
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "系统维护中")
        self.assertEqual(body["upstream"]["body"], '{"code": -1, "msg": "系统维护中", "data": null}')

    def test_areas_rejects_bad_arguments_without_request(self):
        for args, expected in (
            (["library", "areas", "--date", "2026/10/03"], "--date 必须是 YYYY-MM-DD"),
            (["library", "areas", "2026-10-03"], "areas 不接受位置参数"),
            (["library", "areas", "--day", "2026-10-03"], "unknown option: --day"),
            (["library", "areas", "--date"], "--date requires a value"),
        ):
            out = io.StringIO()
            code = run(args, out, io.StringIO())
            self.assertNotEqual(code, 0, args)
            self.assertIn(expected, out.getvalue(), args)
        self.assertEqual(SERVER_STATE["requests"], [])

    def test_unknown_action_and_help(self):
        out = io.StringIO()
        code = run(["library", "seat", "--area", "12"], out, io.StringIO())
        self.assertNotEqual(code, 0)
        self.assertIn("unknown action: seat", out.getvalue())

        out = io.StringIO()
        code = run(["library", "--help"], out, io.StringIO())
        self.assertEqual(code, 2)
        text = out.getvalue()
        self.assertIn("Usage: easy-qfnu library <areas|seats>", text)
        self.assertEqual(SERVER_STATE["requests"], [])


class LibrarySeatsTest(LibraryServerTest):
    def setUp(self):
        super().setUp()
        self.answer(library.SEAT_DATE_PATH, DATE_BODY)
        self.answer(library.SEAT_LIST_PATH, SEAT_BODY)

    def test_seats_resolves_segment_then_lists_statuses(self):
        out = io.StringIO()
        code = run(["library", "seats", "--area", "12", "--date", "2026-10-03"], out, io.StringIO())
        self.assertEqual(code, 0)
        self.assertEqual(self.paths(), [library.SEAT_DATE_PATH, library.SEAT_LIST_PATH])
        self.assertEqual(self.payload(0), {"build_id": 12})
        self.assertEqual(
            self.payload(1),
            {
                "area": 12,
                "segment": 1864001,
                "day": "2026-10-03",
                "startTime": "08:00",
                "endTime": "22:00",
            },
        )
        for headers in SERVER_STATE["headers"]:
            self.assertNotIn("Authorization", headers)
        body = json.loads(out.getvalue())
        self.assertTrue(body["ok"])
        self.assertEqual(body["operation"], "seats")
        self.assertEqual(body["area"], "12")
        self.assertEqual(body["area_name"], "二层自习室")
        self.assertEqual(body["segment"], "1864001")
        self.assertEqual(body["total_num"], 3)
        self.assertEqual(body["free_num"], 1)
        self.assertEqual(body["seat_count"], 3)
        self.assertEqual(body["status_counts"], {"使用中": 1, "已预约": 1, "空闲": 1})
        self.assertEqual(body["items"][0], {
            "id": "3063",
            "no": "001",
            "name": "001",
            "status": "1",
            "status_name": "空闲",
            "area": "12",
            "area_name": "二层自习室",
        })

    def test_free_only_and_explicit_segment_skip_segment_lookup(self):
        out = io.StringIO()
        code = run(
            ["library", "seats", "--area", "12", "--segment", "999", "--date", "2026-10-03", "--free-only"],
            out,
            io.StringIO(),
        )
        self.assertEqual(code, 0)
        self.assertEqual(self.paths(), [library.SEAT_LIST_PATH])
        self.assertEqual(self.payload()["segment"], 999)
        body = json.loads(out.getvalue())
        self.assertTrue(body["free_only"])
        self.assertEqual(body["seat_count"], 1)
        self.assertEqual([item["no"] for item in body["items"]], ["001"])

    def test_seats_without_slot_for_day_fails_with_hint(self):
        out = io.StringIO()
        code = run(["library", "seats", "--area", "12", "--date", "2026-10-02"], out, io.StringIO())
        self.assertNotEqual(code, 0)
        self.assertEqual(self.paths(), [library.SEAT_DATE_PATH])
        body = json.loads(out.getvalue())
        self.assertFalse(body["ok"])
        self.assertIn("2026-10-02 没有可预约时段", body["error"])
        self.assertIn("library areas", body["hint"])

    def test_seats_upstream_rejection_keeps_message(self):
        self.answer(library.SEAT_LIST_PATH, '{"code": 0, "msg": "请选择时段不能为空", "data": []}')
        out = io.StringIO()
        code = run(["library", "seats", "--area", "12", "--date", "2026-10-03"], out, io.StringIO())
        self.assertNotEqual(code, 0)
        body = json.loads(out.getvalue())
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "请选择时段不能为空")
        self.assertEqual(body["upstream"]["body"], '{"code": 0, "msg": "请选择时段不能为空", "data": []}')

    def test_seats_rejects_bad_arguments_without_request(self):
        for args, expected in (
            (["library", "seats"], "seats 必须提供 --area"),
            (["library", "seats", "--area", "12 号"], "--area 必须是数字 id"),
            (["library", "seats", "--area", "12", "--segment", "abc"], "--segment 必须是数字 id"),
            (["library", "seats", "--area", "12", "--date", "10-03"], "--date 必须是 YYYY-MM-DD"),
            (["library", "seats", "--area", "12", "--start-time", "8点"], "--start-time 必须是 HH:MM 格式"),
            (["library", "seats", "--area", "12", "--free"], "unknown option: --free"),
            (["library", "seats", "12"], "seats 不接受位置参数"),
        ):
            out = io.StringIO()
            code = run(args, out, io.StringIO())
            self.assertNotEqual(code, 0, args)
            self.assertIn(expected, out.getvalue(), args)
        self.assertEqual(SERVER_STATE["requests"], [])

    def test_seats_defaults_to_today(self):
        today = datetime.now(timezone.utc).astimezone().date().isoformat()
        self.answer(
            library.SEAT_DATE_PATH,
            '{"code": 1, "msg": "操作成功", "data": [{"day": "' + today + '", "times": [{"id": "7"}]}]}',
        )
        out = io.StringIO()
        code = run(["library", "seats", "--area", "12"], out, io.StringIO())
        self.assertEqual(code, 0)
        body = json.loads(out.getvalue())
        self.assertEqual(body["day"], today)
        self.assertEqual(self.payload()["day"], today)


if __name__ == "__main__":
    unittest.main()
