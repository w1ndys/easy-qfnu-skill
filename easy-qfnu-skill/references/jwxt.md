# QFNU Qiangzhi teaching system

Base: `http://zhjw.qfnu.edu.cn/`

Product: Qiangzhi (`强智`) `jsxsd`

Current coverage: login, session, student profile, course grades, semester schedule, and explicitly confirmed student evaluations. Queries are read-only; do not reconstruct the captcha or encryption flow in the agent; call `scripts/easy-qfnu jwxt`.

## Authentication

Before fetching a captcha or attempting login, inspect a user-supplied password for Chinese or full-width punctuation. If any is present, stop and prominently warn the user in Chinese: punctuation in a normal JWXT password should be English half-width characters; verify the password and retry. Never silently convert punctuation, guess the intended characters, or echo the password.

Login uses 6 sequential steps on **one Cookie jar**:

1. `GET /` — plant session cookies
2. `GET /verifycode.servlet` — download captcha image bytes
3. `POST {OCR}/ocr` — `image=<base64>` → `{code, data, message}`; call this only when an independent OCR service is configured. Model vision skips this step.
4. `POST /Logon.do?method=logon&flag=sess` — empty body → `scode#sxh`
5. `POST /Logon.do?method=logonLdap` — `userAccount=&userPassword=&RANDOMCODE=<captcha>&encoded=<encoded>`
6. `GET /jsxsd/framework/xsMain.jsp` — **do not follow redirects**; success is HTTP 200 plus `教学一体化服务平台` or `glyphicon-class`

Password errors stop immediately. Captcha errors restart from step 1, for at most 3 rounds. This applies both to OCR login and to the agent repeating the manual `captcha` + `login --captcha` pair. Retry network 5xx, 429, and timeout failures up to 3 times with a 1-second delay. If the site says the account is logged in elsewhere, stop; automatic status recovery never retries that case.

`encoded` is `username + "%%%" + password` with `scode` characters inserted using `sxh` digit counts on the first 20 plaintext characters. The encoding is implemented inside the released Go binary.

### Captcha: model vision first

The default path requires no OCR installation:

1. Run `easy-qfnu jwxt captcha --out <png>`. It resets the jar, plants session cookies, downloads the image, writes `<png>`, and persists the jar with `captcha_pending: true`.
2. Read the PNG with model vision.
3. Run `easy-qfnu jwxt login --username <student-id> --password <password> --captcha <captcha-text>`. This skips image download and OCR, reuses the saved jar, fetches `scode#sxh`, builds `encoded`, and submits. A wrong captcha means only that this reading failed. Fetch a new image and retry, up to 3 complete captcha sessions before reporting failure.

When model vision is unavailable, deploy the independent [ddddocr-vercel](https://github.com/w1ndys/ddddocr-vercel) service to Vercel and set `QFNU_OCR_URL` to its root URL. If deployment or access encounters network errors, do not retry automatically. Show the `jwxt captcha` PNG to the user, let the user read it, then run `jwxt login --captcha "<user-reading>"`. Never guess captcha text.

## Independent OCR

The ddddocr Flask service lives in a separate repository and is not bundled with this skill. Deploy `w1ndys/ddddocr-vercel` to Vercel, then set its root URL before using automatic login:

```bash
export QFNU_OCR_URL="https://your-ddddocr-domain.vercel.app"
easy-qfnu jwxt login --username <student-id> --password <password>
```

`QFNU_OCR_URL` and `--ocr-url` both accept the service root URL; the client appends `/ocr`. Contract: `POST /ocr` with form or JSON field `image` containing Base64 image data, returning `{ "code": 200, "data": "abcd", "message": "ok" }`. The service also exposes `GET /health`.

## Session and credentials

Cookies are written to `~/.local/state/easy-qfnu-skill/jwxt-session.json`; override the path with `QFNU_JWXT_COOKIE_PATH`. The file mode is `0600`. Never store the password there or commit the file.

Credentials come from `--username`/`--password`, `QFNU_JWXT_USERNAME`/`QFNU_JWXT_PASSWORD`, or saved credentials. Never echo the password into chat logs.

Credentials are never saved unless explicitly enabled with `--save-credentials yes` or `QFNU_JWXT_SAVE_CREDENTIALS=yes`; `--save-credentials no` is also accepted. Saved credentials are stored at `~/.local/state/easy-qfnu-skill/jwxt-credentials.json`; override the path with `QFNU_JWXT_CREDENTIALS_PATH`. Its parent directory uses mode `0700` and the file uses `0600`.

Credential precedence is command-line arguments, environment variables, then saved credentials. After an expired session, `jwxt status` makes one automatic OCR login attempt only when saved credentials exist and `QFNU_OCR_URL` is configured. Without OCR it returns a manual captcha hint. Wrong passwords and accounts logged in elsewhere stop without another automatic attempt.

## CLI

```bash
easy-qfnu jwxt captcha --out <png>             # model vision or user visual reading
easy-qfnu jwxt login --username <student-id> --password <password> --captcha <captcha-text>
easy-qfnu jwxt login --username <student-id> --password <password>  # independent OCR when QFNU_OCR_URL is set
easy-qfnu jwxt login --save-credentials yes
easy-qfnu jwxt grades --semester 2025-2026-3
easy-qfnu jwxt schedule --semester 2025-2026-3 --week 1
easy-qfnu jwxt evaluations
easy-qfnu jwxt evaluate --score 89                         # preview only
easy-qfnu jwxt evaluate --score 89 --course 0 --confirm    # submit after explicit approval
easy-qfnu jwxt status
easy-qfnu jwxt logout                         # clear the session; preserve credentials
easy-qfnu jwxt logout --forget-credentials   # explicitly clear session and credentials
easy-qfnu jwxt forget-credentials            # clear saved credentials only
```

JSON always includes `ok` and `source: "jwxt"`. Failures contain `ok: false`, `error`, and optionally `hint`.

`jwxt status` and successful `jwxt login` also return `profile`:

| Field | Source |
|---|---|
| `name` | `xsMain_new` label `学生姓名`; fallback to the `glyphicon-class` header in `xsMain.jsp`, excluding `消息通知` and `退出` |
| `student_id` | `学生编号` |
| `college` | `所属院系` |
| `major` | `专业名称` |
| `class_name` | `班级名称` |
| `photo_url` | `/jsxsd/grxx/xszpLoad`, when a photo exists |

Do not fetch the photo binary unless the user asks. Session JSON may cache `name`, `student_id`, `college`, `major`, and `class_name`, but never the password.

## Student evaluation

The evaluation flow follows the confirmed Qiangzhi endpoints from the legacy `easy-qfnu-xspj` project, adapted to the current shared Cookie session:

| Purpose | URL | Method |
|---|---|---|
| Find active evaluation batch (`学生评价`) | `/jsxsd/xspj/xspj_find.do` | GET |
| List course/teacher entries | `/jsxsd/xspj/xspj_list.do?...` | GET, then POST for later pages |
| Read one evaluation form | `/jsxsd/xspj/xspj_edit.do?...` | GET |
| Submit selected indicators | `/jsxsd/xspj/xspj_save.do` | POST, only with `--confirm` |

Run `jwxt evaluations` before `jwxt evaluate`. The latter fetches each pending course, parses hidden form parameters and radio options, and returns a JSON preview with the course ID from that response, course, teacher, indicator selections, and total score. It performs no POST unless `--confirm` is present. The default target is 89; pass `--score` for another target from 0 to 100. Pass `--course` repeatedly or with comma-separated IDs from the list output.

The agent must display the preview in Chinese and obtain explicit approval in the current conversation before adding `--confirm`. The CLI skips entries already marked `已评` or `已提交`. A target of 100 may trigger the system's option restriction; 98 or lower is recommended. It submits each course at most once and stops after the first ambiguous or failed POST so the user can verify the official page. It does not submit free-text comments and does not use the legacy project's “先用 89 分清除系统限制” restriction-bypass behavior.

## Profile pages

| Method | URL | Description |
|---|---|---|
| GET | `/jsxsd/framework/xsMain.jsp` | Successful-login page and sidebar. The header name is in `span.glyphicon-class`. |
| GET | `/jsxsd/framework/xsMain_new.jsp?t1=1` | Personal-center card; primary data source for `status`. |
| GET | `/jsxsd/grxx/xszpLoad` | Student photo binary. |
| GET | `/jsxsd/grsz/grsz_xggrxx.do` | `修改个人信息` form with account, name, and security fields. It has a `保存` button; **never POST it**. |
| GET | `/jsxsd/xsxj/xjxxgl.do` | `学籍信息管理` is a modification-request list, not a student profile card. |

These guessed paths returned `非法访问`: `/jsxsd/grxx/xsxx`, `/jsxsd/grxx/xsxx.do`, `/jsxsd/xsxj/xsxx.do`, `/jsxsd/xsxj/xsxjxx.do`, `/jsxsd/xsxj/toXsxx.do`.

## Probed read-only endpoints

All paths are under `http://zhjw.qfnu.edu.cn`. Reuse the login Cookie jar. Do not follow redirects on authentication-sensitive pages. If the body contains `请输入账号`, `请输入密码`, and `请输入验证码`, the session is expired.

### Confirmed HTTP 200 while logged in

| Purpose | URL | Notes |
|---|---|---|
| Course-grade query shell (`课程成绩查询`) | `/jsxsd/kscj/cjcx_frm` | iframe containing `cjcx_query` and `cjcx_list?kksj=<semester>` |
| Grade list (`成绩列表`) | `/jsxsd/kscj/cjcx_list` | HTML table with semester, course ID/name, group, grade and flag, credits, hours, GPA, retake semester, assessment method/type, and course attributes. Parameter `kksj`, e.g. `2025-2026-3`. CLI: `jwxt grades --semester <semester>`. |
| Standardized-exam grades (`等级考试成绩`) | `/jsxsd/kscj/djkscj_list` | GET 200 |
| Semester schedule (`学期理论课表`) | `/jsxsd/xskb/xskb_list.do` | Read-only GET. Parameters: `xnxq01id` (semester), `zc` (week; empty means all), `sfFD=1`, optional `kbjcmsid` (period scheme). Cells are in `#kbtable` / `.kbcontent`. CLI: `jwxt schedule --semester <semester> [--week <week>]`. |
| Today's homepage schedule fragment (`首页当日课表`) | `/jsxsd/framework/main_index_loadkb.jsp?rq=YYYY-MM-DD` | Loaded by `xsMain_new` using jQuery `.load`; optional `sjmsValue`. |
| Exam arrangements (`考试安排查询`) | `/jsxsd/xsks/xsksap_query` | GET 200 |
| Course-selection center (`学生选课中心`) | `/jsxsd/xsxk/xklc_list` | Read-only inspection of rounds only. Never enter a round and submit a selection. |
| Course-selection results (`选课结果查询`) | `/jsxsd/xkgl/xsxkjgcx` | GET 200 |
| Academic calendar (`教学周历`) | `/jsxsd/jxzl/jxzl_query` | GET 200 |
| Program plan and completion (`培养方案及完成情况`) | `/jsxsd/pyfa/topyfamx` | GET 200; large page |

Complete grade-query iframe paths:

```text
GET /jsxsd/kscj/cjcx_query          # query controls
GET /jsxsd/kscj/cjcx_list?kksj=2025-2026-3
```

Read-only schedule query, equivalent to selecting a semester in the UI:

```text
GET /jsxsd/xskb/xskb_list.do?xnxq01id=2025-2026-3&zc=&sfFD=1&kbjcmsid=94786EE0ABE2D3B2E0531E64A8C09931
```

Use the page's currently selected `kbjcmsid`; never hard-code it.

`jwxt schedule` returns identical `items` and `schedule` arrays. Every non-empty cell contains `day`, the raw `period` text, `course_name` from the first line, detail `lines`, and joined `text`. Skip unknown weekday columns instead of guessing dates.

### Sidebar menu from `xsMain.jsp`

The source menu groups are: `教学评价` / `我的申请` / `我的考试` / `成绩管理` / `培养方案` / `我的课表` / `选课管理` / `教材管理` / `辅修管理` / `实验教学` / `创新学分` / `毕业设计` / `学科竞赛` / `公告留言` / `个人信息` / `在线问答` / `教学周历` / `学籍管理` / `我的成绩` / `毕业管理` / `优秀生转专业`.

Useful read-only `data-url` values, all prefixed with `/jsxsd`:

| Menu item | `data-url` |
|---|---|
| Semester schedule (`学期理论课表`) | `/xskb/xskb_list.do` |
| Lab schedule (`实验课表查询`) | `/syjx/toXskb.do` |
| Cohort/teacher/classroom/course schedules (`班级/教师/教室/课程课表`) | `/kbcx/kbxx_xzb` `/kbcx/kbxx_teacher` `/kbcx/kbxx_classroom` `/kbcx/kbxx_kc` |
| Course grades (`课程成绩查询`) | `/kscj/cjcx_frm` |
| Standardized-exam grades (`等级考试成绩`) | `/kscj/djkscj_list` |
| Exam arrangements (`考试安排查询`) | `/xsks/xsksap_query` |
| Course-selection results (`选课结果查询`) | `/xkgl/xsxkjgcx` |
| Student-status management (`学籍信息管理`) | `/xsxj/xjxxgl.do` |
| Academic warning (`学业预警查询`) | `/xsxj/xsyjxx.do` |
| Program plan and completion (`培养方案及完成情况`) | `/pyfa/topyfamx` |
| Academic calendar (`教学周历查看`) | `/jxzl/jxzl_query` |

Never submit write or application workflows exposed by the menu except the dedicated, explicitly confirmed `jwxt evaluate` flow: deferred-exam requests, make-up exam registration, actual course selection (`/jsxsd/xsxk/xklc_list`), preselection or any other enrollment action, textbook confirmation, minor enrollment/withdrawal, lab reservation, innovation-credit application, thesis uploads, major-change requests, personal-information saves, or student-status edits (`toEditxsxx.do`). The separate public `precourse` catalog query is read-only and does not authorize any of these actions.

## Out of scope

Course selection, preselection, personal-information saves, and every business-form submission other than the explicitly confirmed `jwxt evaluate` command are prohibited. The public `precourse` catalog is a separate read-only snapshot query. Grade and schedule queries remain read-only CLI operations. Library-seat queries are not implemented yet.
