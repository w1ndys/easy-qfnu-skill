# Public pre-course catalog

The public pre-course catalog is a **cached, read-only snapshot**. It does not require a JWXT session and it cannot select or preselect a course.

There is a separate **live** catalog: `easy-qfnu jwxt xk search`. That command needs a logged-in JWXT session and an open course-selection round. Always tell the user which one you used:

- `precourse search` = 缓存预选课查询，定时快照，可能滞后，不必登录
- `jwxt xk search` = 即时查询，当前轮次教务库，更准确及时，能探测课程所在选课模块；网页可能按年级隐藏模块，该查询不受此限制。只读，不会选课

If no round is open, say 即时查询不可用 and use this cached catalog.

## CLI

```bash
easy-qfnu precourse search "音乐鉴赏"
easy-qfnu precourse search --teacher-name "王" --campus "日照"
easy-qfnu precourse meta
easy-qfnu precourse popular --field teacherName
```

`search` accepts one positional keyword, which maps to `q`, and optional filters:

| CLI option | Upstream field |
|---|---|
| `--q` | `q` |
| `--course-code` | `courseCode` |
| `--course-name` | `courseName` |
| `--teacher-name` | `teacherName` |
| `--course-nature` | `courseNature` |
| `--course-attr` | `courseAttr` |
| `--college` | `college` |
| `--schedule-time` | `scheduleTime` |
| `--location` | `location` |
| `--campus` | `campus` |

At least one non-empty search condition is required. The service returns at most 500 courses. `q` is an OR match across course code, course name, and teacher name; the other supplied filters are combined with AND.

## Response and freshness

The CLI returns JSON with `ok`, `source: "precourse"`, `operation`, and the upstream `data` fields. Search results normally contain `count` and `courses`; `meta` contains `systemInfo`; `popular` contains `field` and `items`.

Use `meta` when the user asks when the data was updated. Explain that the source is a scheduled teaching-system snapshot: selected counts can be delayed, and the result is not proof of a current enrollment outcome.

## Safety

- Never request, print, or configure the upstream API key in a user session.
- Never send a JWXT Cookie for this public query.
- Do not turn catalog results into a course-selection or preselection submission.
- If the service returns `ok: false`, show its `error` and `hint` and stop.
