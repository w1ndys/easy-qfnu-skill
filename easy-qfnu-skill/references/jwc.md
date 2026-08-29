# QFNU Academic Affairs Office website

Public CMS: Westsoft Visual SiteBuilder 9 (`西软 Visual SiteBuilder 9`)

Base: `https://jwc.qfnu.edu.cn/`

Owner / site id: `1485250630`

No login. Pages are statically published `.htm` files plus a Lucene search JSP.

## URL rules

List page 1: `/{slug}.htm`  
Later pages: `/{slug}/{N}.htm`

Pager text looks like `共1432条 1/144` (“1,432 items, page 1 of 144”). For published static lists (`pub_mode=2`):

- User page 1 → `/{slug}.htm`
- User page P (1 < P < totalPages) → `/{slug}/{totalPages - P + 1}.htm`
- Last page → `/{slug}/1.htm`

Example: when the notices feed (`通知`) has 144 pages, user page 2 is `tz_j_/143.htm`.

Article: `/info/<categoryId>/<newsId>.htm`  
Attachment: `/system/_content/download.jsp?urltype=news.DownloadAttachUrl&owner=1485250630&wbfileid=<id>`

Do not treat `content.jsp?urltype=news.NewsContentUrl&wbtreeid=<id>&wbnewsid=<id>` as a stable public URL. Newly inserted items sometimes appear in lists with that form before a static `/info/<id>/<id>.htm` is published. Fetching it returns a CMS “系统提示” login wall; the matching `/info/...` path often returns 404. The CLI keeps the original URL and sets `unpublished: true`.


## Channels

| key | title | slug |
|---|---|---|
| notices | Important notices (`重要通知`) | tz_j_ |
| announcements | Department announcements (`部门公告`) | gg_j_ |
| news | News (`新闻`) | xw_j_ |
| jxyj-notices | Teaching-research notices (`教学研究通知`) | jxyj/jxyjtz |
| jxyj-announcements | Teaching-research announcements (`教学研究公告`) | jxyj/jxyjgg |
| jxyj-news | Teaching-research news (`教学研究新闻`) | jxyj/jxyjxw |
| jwyx-notices | Academic-operations notices (`教务运行通知`) | jwyx/jwyxtz |
| jwyx-announcements | Academic-operations announcements (`教务运行公告`) | jwyx/jwyxgg |
| jwyx-news | Academic-operations news (`教务运行新闻`) | jwyx/jwyxxw |
| xjgl-notices | Student-status notices (`学籍管理通知`) | xjgl/xjgltz |
| xjgl-announcements | Student-status announcements (`学籍管理公告`) | xjgl/xjglgg |
| xjgl-news | Student-status news (`学籍管理新闻`) | xjgl/xjglxw |
| sjjx-notices | Practical-teaching notices (`实践教学通知`) | sjjx/sjjxtz |
| sjjx-announcements | Practical-teaching announcements (`实践教学公告`) | sjjx/sjjxgg |
| sjjx-news | Practical-teaching news (`实践教学新闻`) | sjjx/sjjxxw |
| jsfz-notices | Faculty-development notices (`教师发展通知`) | jsfz/jsfztz |
| jsfz-announcements | Faculty-development announcements (`教师发展公告`) | jsfz/jsfzgg |
| jsfz-news | Faculty-development news (`教师发展新闻`) | jsfz/jsfzxw |
| kcsz-notices | Curriculum ideology notices (`课程思政通知`) | kcsz/kcsztz |
| kcsz-announcements | Curriculum ideology announcements (`课程思政公告`) | kcsz/kcszgg |
| kcsz-news | Curriculum ideology news (`课程思政新闻`) | kcsz/kcszxw |

`notices` / `announcements` / `news` are homepage aggregates labeled “旧” in the page title; they still contain the active important-notice, department-announcement, and news feeds.

## List HTML

Items live in `ul.n_listxx1` / `ul.n_listxx`:

```html
<li id="line_u6_0">
  <h2 class="cleafix">
    <a href="info/1103/7719.htm" title="...">标题</a>
    <span class="time">2026-07-13</span>
  </h2>
  <p>摘要...[<a href="info/1103/7719.htm">详细</a>]</p>
</li>
```

Subdirectory lists (e.g. `jwyx/jwyxtz.htm`) use relative `../info/...` hrefs. Always resolve against the page URL.

## Article HTML

- Title: `form[name=_newscontent_fromname] h2`
- Date: `发布时间：YYYY-MM-DD`
- Section: breadcrumb in `.n_tit` (e.g. 首页 > 教务运行 > 教务运行通知 > 正文)
- Body: `#vsb_content .v_news_content`
- Editor: `.con_bm` `责任编辑：...`
- Attachments: `a[href*=DownloadAttachUrl]`
- Click count is JS (`_showDynClicks`) and is not worth scraping.

Body text is often split across many `<span>`s. Convert HTML to text instead of copying inner HTML.

## Search

`POST /ssjg.jsp?wbtreeid=1001`

- `lucenenewssearchkey`: Base64 of the keyword (UTF-8)
- `_lucenesearchtype=1`
- `searchScope=1`

Later pages: `GET /ssjg.jsp?wbtreeid=1001&searchScope=1&currentnum=<page>&newskeycode2=<url-encoded-base64>`

Result markup matches the list parser (`h2 a` + `span.time` + summary `p`).

## CLI mapping

```bash
python3 scripts/qfnu jwc list --channel <key> [--page N] [--limit N]
python3 scripts/qfnu jwc get <url-or-info/category/id.htm>
python3 scripts/qfnu jwc search "<keyword>" [--page N] [--limit N]
python3 scripts/qfnu jwc channels
```

`--limit` on page 1 of a list may fetch extra static pages so the caller can return more than one CMS page (default page size is 10).

JSON always includes `ok`. Failures use `ok: false`, `error`, optional `hint`.
