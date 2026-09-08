"""匿名用量上报。只发功能名和成败，不含学号、Cookie 或其它身份信息。"""

import json
import platform
import sys
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .version import VERSION

TELEMETRY_ENDPOINT = "https://hub.easy-qfnu.top/v1/telemetry/events"
TELEMETRY_TIMEOUT_SECONDS = 2
LOGIN_NOTICE = "已触发匿名登录时间上报；不含学号、姓名、Cookie 或其他身份信息"


def current_os():
    """映射到与原 CLI 相近的系统名，便于 hub 按端统计。"""
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform.startswith("win"):
        return "windows"
    return sys.platform


def current_arch():
    """映射到与原 CLI 相近的架构名。"""
    machine = platform.machine().lower()
    if machine == "x86_64" or machine == "amd64":
        return "amd64"
    if machine == "aarch64" or machine == "arm64":
        return "arm64"
    if machine == "i386" or machine == "i686" or machine == "x86":
        return "386"
    if machine == "":
        return "unknown"
    return machine


def usage_status(err):
    """命令错误映射为 success/failure。有错误就是 failure。"""
    if err is None:
        return "success"
    return "failure"


def build_event(feature, status, occurred_at):
    """组装上报字段。不要在这里加入学号、Cookie 或请求头身份信息。"""
    return {
        "feature": feature,
        "status": status,
        "cli_version": VERSION,
        "os": current_os(),
        "arch": current_arch(),
        "occurred_at": occurred_at,
    }


class TelemetryClient:
    def __init__(self, endpoint, now):
        self.endpoint = endpoint
        self.now = now

    def send(self, feature, status):
        """POST 匿名事件。HTTP 非 2xx 或网络失败时抛错，由上层吞掉。"""
        occurred_at = self.now().astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = json.dumps(build_event(feature, status, occurred_at), ensure_ascii=False)
        request = Request(self.endpoint, data=body.encode("utf-8"), method="POST")
        request.add_header("Content-Type", "application/json")
        request.add_header("User-Agent", "easy-qfnu/" + VERSION)
        self._post(request)

    def _post(self, request):
        try:
            response = urlopen(request, timeout=TELEMETRY_TIMEOUT_SECONDS)
        except HTTPError as exc:
            raise ValueError(f"telemetry HTTP {exc.code}") from exc
        except URLError as exc:
            raise OSError(str(exc.reason)) from exc
        try:
            response.read()
            status = response.getcode() or 0
        finally:
            response.close()
        if status < 200 or status >= 300:
            raise ValueError(f"telemetry HTTP {status}")


def send_anonymous_event(feature, status):
    client = TelemetryClient(TELEMETRY_ENDPOINT, datetime.now)
    client.send(feature, status)


report_anonymous_event = send_anonymous_event


def report_usage(feature, status):
    """旁路上报。失败不能改变命令结果。"""
    try:
        report_anonymous_event(feature, status)
    except (OSError, ValueError, TypeError):
        return


def report_login_success(result):
    """登录成功后写说明并上报。上报失败只静默返回，不把错误写进结果。"""
    result["telemetry_notice"] = LOGIN_NOTICE
    try:
        report_anonymous_event("jwxt.login", "success")
    except (OSError, ValueError, TypeError):
        return
