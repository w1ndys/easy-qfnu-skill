"""教务处公告：栏目列表、检索、正文。只读，不上登录态。"""

import base64
import html
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlparse
from urllib.request import Request, urlopen

from . import telemetry
from .result import failure, success, write_json

JWC_BASE = "https://jwc.qfnu.edu.cn"
USER_AGENT = "easy-qfnu-skill/easy-qfnu"
REQUEST_TIMEOUT = 30

CHANNELS = [
    {"key": "notices", "title": "Important notices (重要通知)", "kind": "aggregate", "slug": "tz_j_"},
    {"key": "announcements", "title": "Department announcements (部门公告)", "kind": "aggregate", "slug": "gg_j_"},
    {"key": "news", "title": "News (新闻)", "kind": "aggregate", "slug": "xw_j_"},
    {"key": "jxyj-notices", "title": "Teaching-research notices (教学研究通知)", "kind": "category", "slug": "jxyj/jxyjtz"},
    {"key": "jxyj-announcements", "title": "Teaching-research announcements (教学研究公告)", "kind": "category", "slug": "jxyj/jxyjgg"},
    {"key": "jxyj-news", "title": "Teaching-research news (教学研究新闻)", "kind": "category", "slug": "jxyj/jxyjxw"},
    {"key": "jwyx-notices", "title": "Academic-operations notices (教务运行通知)", "kind": "category", "slug": "jwyx/jwyxtz"},
    {"key": "jwyx-announcements", "title": "Academic-operations announcements (教务运行公告)", "kind": "category", "slug": "jwyx/jwyxgg"},
    {"key": "jwyx-news", "title": "Academic-operations news (教务运行新闻)", "kind": "category", "slug": "jwyx/jwyxxw"},
    {"key": "xjgl-notices", "title": "Student-status notices (学籍管理通知)", "kind": "category", "slug": "xjgl/xjgltz"},
    {"key": "xjgl-announcements", "title": "Student-status announcements (学籍管理公告)", "kind": "category", "slug": "xjgl/xjglgg"},
    {"key": "xjgl-news", "title": "Student-status news (学籍管理新闻)", "kind": "category", "slug": "xjgl/xjglxw"},
    {"key": "sjjx-notices", "title": "Practical-teaching notices (实践教学通知)", "kind": "category", "slug": "sjjx/sjjxtz"},
    {"key": "sjjx-announcements", "title": "Practical-teaching announcements (实践教学公告)", "kind": "category", "slug": "sjjx/sjjxgg"},
    {"key": "sjjx-news", "title": "Practical-teaching news (实践教学新闻)", "kind": "category", "slug": "sjjx/sjjxxw"},
    {"key": "jsfz-notices", "title": "Faculty-development notices (教师发展通知)", "kind": "category", "slug": "jsfz/jsfztz"},
    {"key": "jsfz-announcements", "title": "Faculty-development announcements (教师发展公告)", "kind": "category", "slug": "jsfz/jsfzgg"},
    {"key": "jsfz-news", "title": "Faculty-development news (教师发展新闻)", "kind": "category", "slug": "jsfz/jsfzxw"},
    {"key": "kcsz-notices", "title": "Curriculum ideology notices (课程思政通知)", "kind": "category", "slug": "kcsz/kcsztz"},
    {"key": "kcsz-announcements", "title": "Curriculum ideology announcements (课程思政公告)", "kind": "category", "slug": "kcsz/kcszgg"},
    {"key": "kcsz-news", "title": "Curriculum ideology news (课程思政新闻)", "kind": "category", "slug": "kcsz/kcszxw"},
]

LI_RE = re.compile(r"(?is)<li\b[^>]*>(.*?)</li\s*>")
H2_RE = re.compile(r"(?is)<h2\b[^>]*>(.*?)</h2\s*>")
A_RE = re.compile(r"(?is)<a\b([^>]*)>(.*?)</a\s*>")
P_RE = re.compile(r"(?is)<p\b[^>]*>(.*?)</p\s*>")
TITLE_RE = re.compile(r"(?is)<title\b[^>]*>(.*?)</title\s*>")
INFO_RE = re.compile(r"(?i)(?:/|^)info/(\d+)/(\d+)(?:\.htm)?")
DATE_RE = re.compile(r"\b(20\d{2}[-/.]\d{1,2}[-/.]\d{1,2})\b")
PAGE_RE = re.compile(r"(?i)(?:第\s*\d+\s*/\s*|页次\s*[:：]\s*\d+\s*/\s*)(\d+)")
ARTICLE_HEADER_RE = re.compile(
    r"(?is)<form\b[^>]*name=[\"']_newscontent_fromname[\"'][^>]*>.*?<h2\b[^>]*>(.*?)</h2>"
)
ARTICLE_DATE_RE = re.compile(r"发布时间\s*[:：]?\s*(20\d{2}[-/.]\d{1,2}[-/.]\d{1,2})")
ARTICLE_CONTENT_RE = re.compile(r"(?is)<div\b[^>]*id=[\"']vsb_content[\"'][^>]*>(.*?)</div>")
SCRIPT_RE = re.compile(r"(?is)<script\b[^>]*>.*?</script\s*>")
STYLE_RE = re.compile(r"(?is)<style\b[^>]*>.*?</style\s*>")
BR_RE = re.compile(r"(?i)<br\s*/?>")
TAG_RE = re.compile(r"(?is)<[^>]+>")


class JWCError(Exception):
    def __init__(self, message, hint=""):
        super().__init__(message)
        self.message = message
        self.hint = hint


def clean_html(fragment):
    fragment = SCRIPT_RE.sub(" ", fragment)
    fragment = STYLE_RE.sub(" ", fragment)
    fragment = BR_RE.sub("\n", fragment)
    fragment = TAG_RE.sub(" ", fragment)
    fragment = html.unescape(fragment)
    fragment = fragment.replace("\u00a0", " ")
    return " ".join(fragment.split()).strip()


def attr(tag, name):
    quoted = re.compile(
        r"(?is)\b" + re.escape(name) + r'\s*=\s*"([^"]*)"|\b' + re.escape(name) + r"\s*=\s*'([^']*)'"
    )
    match = quoted.search(tag)
    if match:
        if match.group(1):
            return html.unescape(match.group(1))
        return html.unescape(match.group(2) or "")
    unquoted = re.compile(r"(?is)\b" + re.escape(name) + r"\s*=\s*([^\s>]+)")
    match = unquoted.search(tag)
    if match:
        return html.unescape(match.group(1).strip("\"'"))
    return ""


def resolve_channel(name):
    name = name.strip()
    for item in CHANNELS:
        if item["key"] == name or item["title"] == name or name in item["title"]:
            return item
    raise JWCError(
        "unknown JWC channel: " + name,
        "run easy-qfnu jwc channels to list supported channels",
    )


def parse_list_items(raw, page_url):
    items = []
    for fragment in LI_RE.findall(raw):
        item = parse_list_item(fragment, page_url)
        if item is not None:
            items.append(item)
    return items


def parse_list_item(fragment, page_url):
    h2 = H2_RE.search(fragment)
    if h2 is None:
        return None
    links = A_RE.findall(h2.group(1))
    if len(links) == 0:
        return None
    attrs, inner = links[0]
    href = attr(attrs, "href")
    resolved = urljoin(page_url, href)
    title = attr(attrs, "title").strip()
    if title == "":
        title = clean_html(inner)
    date = parse_item_date(fragment)
    summary = ""
    paragraph = P_RE.search(fragment)
    if paragraph:
        summary = clean_html(paragraph.group(1))
    category, item_id = parse_info_id(resolved)
    return {
        "id": item_id,
        "category_id": category,
        "title": title,
        "date": date,
        "url": resolved,
        "summary": summary,
        "unpublished": "content.jsp" in resolved.lower(),
    }


def parse_item_date(fragment):
    match = DATE_RE.search(clean_html(fragment))
    if match is None:
        return ""
    return match.group(1).replace("/", "-").replace(".", "-")


def parse_info_id(resolved):
    match = INFO_RE.search(resolved)
    if match is None:
        return "", ""
    return match.group(1), match.group(2)


def parse_integer(option, value):
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(option + " must be an integer") from exc


def take_limit(rows, limit):
    if limit > 0 and len(rows) > limit:
        return rows[:limit]
    return rows


def parse_list_options(args):
    channel_name = "notices"
    page = 1
    limit = 10
    index = 0
    while index < len(args):
        arg = args[index]
        if index + 1 >= len(args):
            raise ValueError(arg + " requires a value")
        value = args[index + 1]
        if arg == "--channel" or arg == "-c":
            channel_name = value
        elif arg == "--page":
            page = parse_integer(arg, value)
        elif arg == "--limit":
            limit = parse_integer(arg, value)
        else:
            raise ValueError("unknown option: " + arg)
        index += 2
    return channel_name, page, limit


def list_jwc(args):
    channel_name, page, limit = parse_list_options(args)
    item = resolve_channel(channel_name)
    try:
        raw, _final_url = list_jwc_page(item, page)
    except (OSError, ValueError, JWCError) as exc:
        raise JWCError("failed to fetch JWC list: " + str(exc), "请检查网络后重试") from exc
    rows = take_limit(parse_list_items(raw, JWC_BASE + list_path(item, page)), limit)
    return success(
        "jwc",
        {
            "channel": item["key"],
            "title": item["title"],
            "kind": item["kind"],
            "page": page,
            "limit": limit,
            "total": None,
            "total_pages": page,
            "count": len(rows),
            "items": rows,
        },
    )


def list_path(item, page):
    if page < 1:
        raise JWCError("page must be at least 1")
    if page > 1:
        return "/" + item["slug"] + "/" + str(page) + ".htm"
    return "/" + item["slug"] + ".htm"


def list_jwc_page(item, page):
    return request_jwc("GET", JWC_BASE + list_path(item, page), None, None)


def parse_search_options(args):
    if len(args) == 0 or args[0].startswith("-"):
        raise JWCError("search keyword is empty")
    keyword = args[0]
    page = 1
    limit = 10
    index = 1
    while index < len(args):
        arg = args[index]
        if index + 1 >= len(args):
            raise ValueError(arg + " requires a value")
        if arg == "--page" or arg == "--limit":
            parsed = parse_integer(arg, args[index + 1])
            if arg == "--page":
                page = parsed
            else:
                limit = parsed
        else:
            raise ValueError("unknown option: " + arg)
        index += 2
    return keyword, page, limit


def search_jwc(args):
    keyword, page, limit = parse_search_options(args)
    try:
        raw, final_url = search_jwc_page(keyword, page)
    except (OSError, ValueError, JWCError) as exc:
        raise JWCError("failed to search JWC: " + str(exc), "请检查网络后重试") from exc
    rows = take_limit(parse_list_items(raw, final_url), limit)
    return success(
        "jwc",
        {
            "query": keyword.strip(),
            "page": page,
            "limit": limit,
            "total": None,
            "total_pages": parse_page_count(raw),
            "count": len(rows),
            "items": rows,
            "url": final_url,
        },
    )


def parse_page_count(raw):
    match = PAGE_RE.search(clean_html(raw))
    if match is None:
        return 1
    try:
        parsed = int(match.group(1))
    except ValueError as exc:
        raise JWCError("invalid JWC pagination metadata", "请稍后重试") from exc
    if parsed < 1:
        raise JWCError("invalid JWC pagination metadata", "请稍后重试")
    return parsed


def search_jwc_page(keyword, page):
    encoded = base64.b64encode(keyword.strip().encode("utf-8")).decode("ascii")
    if page <= 1:
        form = "lucenenewssearchkey=" + quote(encoded) + "&_lucenesearchtype=1&searchScope=1"
        return request_jwc(
            "POST",
            JWC_BASE + "/ssjg.jsp?wbtreeid=1001",
            form.encode("utf-8"),
            {
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": JWC_BASE + "/",
            },
        )
    target = (
        JWC_BASE
        + "/ssjg.jsp?wbtreeid=1001&searchScope=1&currentnum="
        + str(page)
        + "&newskeycode2="
        + quote(encoded)
    )
    return request_jwc("GET", target, None, {"Referer": JWC_BASE + "/"})


def article_jwc(target):
    resolved = resolve_article_url(target)
    try:
        raw, final_url = request_jwc("GET", resolved, None, None)
    except (OSError, ValueError) as exc:
        raise JWCError("failed to fetch article: " + str(exc), "请检查文章地址或网络连接") from exc
    # 草稿页只有系统提示、没有正文容器，不能当已发布公告返回。
    if "系统提示" in raw and "vsb_content" not in raw:
        raise JWCError(
            "article is not publicly readable: " + resolved,
            "该文章仍是 content.jsp 草稿，正文需要登录后才能查看",
        )
    return parse_article(raw, final_url)


def resolve_article_url(target):
    target = target.strip()
    resolved = target
    if not target.startswith("http://") and not target.startswith("https://"):
        resolved = join_article_path(target)
    parsed = urlparse(resolved)
    if parsed.hostname != "jwc.qfnu.edu.cn":
        raise JWCError("refusing non-JWC URL: " + resolved)
    return resolved


def join_article_path(target):
    if "content.jsp" not in target:
        if not target.startswith("/"):
            target = "/" + target
        if not target.endswith(".htm"):
            target += ".htm"
    return urljoin(JWC_BASE + "/", target)


def parse_article(raw, final_url):
    title = parse_article_title(raw)
    if title == "":
        raise JWCError("could not parse article at " + final_url)
    date = ""
    date_match = ARTICLE_DATE_RE.search(clean_html(raw))
    if date_match:
        date = date_match.group(1).replace("/", "-").replace(".", "-")
    content = parse_article_content(raw)
    category, item_id = parse_info_id(final_url)
    return success(
        "jwc",
        {
            "id": item_id,
            "category_id": category,
            "title": title,
            "date": date,
            "editor": "",
            "section": "",
            "breadcrumb": [],
            "url": final_url,
            "content_text": content,
            "attachments": [],
        },
    )


def parse_article_title(raw):
    match = ARTICLE_HEADER_RE.search(raw)
    if match:
        title = clean_html(match.group(1))
        if title != "":
            return title
    title_match = TITLE_RE.search(raw)
    if title_match:
        return clean_html(title_match.group(1))
    return ""


def parse_article_content(raw):
    match = ARTICLE_CONTENT_RE.search(raw)
    if match:
        content = clean_html(match.group(1))
        if content != "":
            return content
    return clean_html(raw)


def request_jwc(method, target, body, headers):
    request = Request(target, data=body, method=method)
    request.add_header("User-Agent", USER_AGENT)
    if headers:
        for key in headers:
            request.add_header(key, headers[key])
    try:
        response = urlopen(request, timeout=REQUEST_TIMEOUT)
    except HTTPError as exc:
        raise OSError("HTTP " + str(exc.code)) from exc
    except URLError as exc:
        raise OSError(str(exc.reason)) from exc
    try:
        data = response.read()
        status = response.getcode() or 0
        final_url = response.geturl()
    finally:
        response.close()
    if status < 200 or status >= 300:
        raise OSError("HTTP " + str(status))
    return data.decode("utf-8", errors="replace"), final_url


def run_jwc(args, out):
    if len(args) == 0 or args[0] == "--help":
        return usage_jwc(out)
    result = None
    err = None
    feature = ""
    try:
        result, feature = dispatch_jwc(args)
    except (JWCError, ValueError, OSError, TypeError) as caught:
        err = caught
        feature = feature_for(args[0])
    if feature != "":
        telemetry.report_usage("jwc." + feature, telemetry.usage_status(err))
    if err is not None:
        return write_json(out, jwc_failure(err))
    return write_json(out, result)


def dispatch_jwc(args):
    action = args[0]
    if action == "channels":
        return success("jwc", {"channels": CHANNELS}), ""
    if action == "list":
        return list_jwc(args[1:]), "list"
    if action == "search":
        return search_jwc(args[1:]), "search"
    if action == "get":
        if len(args) < 2:
            raise ValueError("get requires a URL or info path")
        return article_jwc(args[1]), "get"
    raise ValueError("unknown action: " + action)


def feature_for(action):
    if action == "list" or action == "search" or action == "get":
        return action
    return ""


def jwc_failure(err):
    if isinstance(err, JWCError):
        return failure("jwc", err.message, err.hint)
    return failure("jwc", str(err), "")


def usage_jwc(out):
    try:
        out.write("Usage: easy-qfnu jwc <list|get|search|channels> [options]\n")
    except OSError:
        return 1
    return 2
