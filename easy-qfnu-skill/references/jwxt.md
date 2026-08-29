# 曲阜师范大学强智教务系统

Base: `http://zhjw.qfnu.edu.cn/`  
Product: 强智 `jsxsd`

Read-only skill coverage today: login + session + student profile. Do not reconstruct the captcha/encryption flow in the agent; call `scripts/qfnu jwxt`.

## Auth

Login is 6 sequential steps on **one Cookie jar**:

1. `GET /` — plant session cookies
2. `GET /verifycode.servlet` — captcha image bytes
3. `POST {OCR}/ocr` — `image=<base64>` → `{code, data, message}`（仅配置独立 OCR 服务时调用；识图路径跳过此步，由模型读图）
4. `POST /Logon.do?method=logon&flag=sess` — empty body → `scode#sxh`
5. `POST /Logon.do?method=logonLdap` — `userAccount=&userPassword=&RANDOMCODE=<captcha>&encoded=<encoded>`
6. `GET /jsxsd/framework/xsMain.jsp` — **do not follow redirects**; 200 plus `教学一体化服务平台` or `glyphicon-class`

Password errors stop immediately. Captcha errors restart from step 1, max 3 rounds; this rule applies to OCR login and to the agent repeating the manual captcha + login --captcha pair. Network 5xx/429/timeout retries 3 times with 1s delay. If the site says the account logged in elsewhere, stop; automatic status recovery never retries this case.

`encoded` is `username + "%%%" + password` with `scode` characters inserted using `sxh` digit counts on the first 20 plaintext characters. Implementation: `encode_credentials` in `scripts/qfnu_jwxt.py`.

### Captcha: model vision first（默认）

Default login path, no OCR install needed:

1. `python3 scripts/qfnu jwxt captcha --out <png>` — resets the jar, plants session cookies, downloads the captcha image, writes it to `<png>`, and persists the jar with `captcha_pending: true`.
2. The agent reads the PNG with model vision.
3. `python3 scripts/qfnu jwxt login --username <学号> --password <密码> --captcha <识图结果>` — skips captcha fetch and OCR, reuses the persisted jar, fetches `scode#sxh`, builds `encoded`, submits. Wrong captcha only means this recognition missed; run `jwxt captcha` again for a new image and retry, up to 3 complete captcha sessions before reporting failure.

When the model has no vision capability, deploy the independent [ddddocr-vercel](https://github.com/w1ndys/ddddocr-vercel) service to Vercel and set `QFNU_OCR_URL` to its root URL. If deployment or access hits network errors, do not auto-retry: show the `jwxt captcha` PNG to the user, let the user reply with the characters, and run `jwxt login --captcha "<用户读出的字符>"`. Never guess captcha text.

## 独立 OCR（模型没有识图能力时才需要）

The ddddocr Flask service lives in its own repository and is not bundled into this skill. Deploy `w1ndys/ddddocr-vercel` to Vercel, then set the service root URL before using the automatic login path.

```bash
export QFNU_OCR_URL="https://你的-ddddocr-域名.vercel.app"
python3 scripts/qfnu jwxt login --username <学号> --password <密码>
```

`QFNU_OCR_URL` and `--ocr-url` both accept the service root URL; the client appends `/ocr`. Contract: `POST /ocr` form field or JSON field `image` (Base64), response `{ "code": 200, "data": "abcd", "message": "ok" }`. The independent service also exposes `GET /health`.

## Session

Cookies are written to `~/.local/state/easy-qfnu-skill/jwxt-session.json` (override with `QFNU_JWXT_COOKIE_PATH`). Mode `0600`. Never store the password. Never commit this file.

Credentials come from `--username/--password` or `QFNU_JWXT_USERNAME` / `QFNU_JWXT_PASSWORD`. Do not echo the password into chat logs.

On the first interactive login, the CLI asks whether to save the credentials. Only an explicit `yes` saves them; pass `--save-credentials yes/no` to make the choice explicit. Non-interactive runs default to no unless `QFNU_JWXT_SAVE_CREDENTIALS=yes` is set. Saved credentials are stored at
`~/.local/state/easy-qfnu-skill/jwxt-credentials.json` (override with `QFNU_JWXT_CREDENTIALS_PATH`), in a 0700 directory and 0600 file.

Login credential precedence is command-line arguments, then environment variables, then saved credentials. `jwxt status` makes one automatic OCR login attempt after an expired session only when saved credentials exist and `QFNU_OCR_URL` is configured. Without OCR it returns a manual captcha hint. Wrong passwords and accounts kicked offline stop without another automatic attempt.

## CLI

```bash
python3 scripts/qfnu jwxt captcha --out <png>             # 默认：模型识图 / 用户肉眼识图
python3 scripts/qfnu jwxt login --username <学号> --password <密码> --captcha <识图结果>
python3 scripts/qfnu jwxt login --username <学号> --password <密码>  # 已配置 QFNU_OCR_URL 时使用独立服务
python3 scripts/qfnu jwxt login --save-credentials yes
python3 scripts/qfnu jwxt grades --semester 2025-2026-3
python3 scripts/qfnu jwxt schedule --semester 2025-2026-3 --week 1
python3 scripts/qfnu jwxt status
python3 scripts/qfnu jwxt logout                         # 只清除会话，默认保留凭据
python3 scripts/qfnu jwxt logout --forget-credentials   # 明确同时清除凭据
python3 scripts/qfnu jwxt forget-credentials            # 只清除凭据
```

JSON always includes `ok` and `source: "jwxt"`. Failures: `ok: false`, `error`, optional `hint`.

`jwxt status` / successful `jwxt login` also return `profile`:

| 字段 | 来源 |
|---|---|
| `name` | `xsMain_new`「学生姓名」；回退 `xsMain.jsp` 顶栏 `glyphicon-class`（跳过「消息通知/退出」） |
| `student_id` | 「学生编号」 |
| `college` | 「所属院系」 |
| `major` | 「专业名称」 |
| `class_name` | 「班级名称」 |
| `photo_url` | `/jsxsd/grxx/xszpLoad`（有照片时） |

Do not fetch the photo binary unless the user asked. Session JSON may cache name/student_id/college/major/class_name; never the password.

## Profile pages

| 方法 | URL | 说明 |
|---|---|---|
| GET | `/jsxsd/framework/xsMain.jsp` | 登录成功页 + 侧栏菜单。顶栏姓名在 `span.glyphicon-class`。 |
| GET | `/jsxsd/framework/xsMain_new.jsp?t1=1` | 个人中心卡片，`status` 的主数据源。 |
| GET | `/jsxsd/grxx/xszpLoad` | 学生照片二进制。 |
| GET | `/jsxsd/grsz/grsz_xggrxx.do` | 「修改个人信息」表单，仅含账号/姓名/密保。有「保存」按钮，**禁止 POST**。 |
| GET | `/jsxsd/xsxj/xjxxgl.do` | 「学籍信息管理」是修改申请列表，不是学籍卡片。 |

These guessed paths returned 「非法访问」: `/jsxsd/grxx/xsxx`, `/jsxsd/grxx/xsxx.do`, `/jsxsd/xsxj/xsxx.do`, `/jsxsd/xsxj/xsxjxx.do`, `/jsxsd/xsxj/toXsxx.do`.

## Read-only endpoints probed

All under `http://zhjw.qfnu.edu.cn`. Reuse the login Cookie jar. Do not follow redirects on auth-sensitive pages. If the body has `请输入账号` + `请输入密码` + `请输入验证码`, the session is dead.

### Confirmed GET 200 (logged in)

| 用途 | URL | 备注 |
|---|---|---|
| 课程成绩查询壳 | `/jsxsd/kscj/cjcx_frm` | iframe：`cjcx_query` + `cjcx_list?kksj=<学年学期>` |
| 成绩列表 | `/jsxsd/kscj/cjcx_list` | HTML 表。列：开课学期/课程编号/课程名称/分组名/成绩/成绩标识/学分/总学时/绩点/补重学期/考核方式/考试性质/课程属性/课程性质/课程类别。学期参数 `kksj`（如 `2025-2026-3`）。CLI：`jwxt grades --semester <学年学期>`。 |
| 等级考试成绩 | `/jsxsd/kscj/djkscj_list` | GET 200 |
| 学期理论课表 | `/jsxsd/xskb/xskb_list.do` | 只读 GET。参数：`xnxq01id`（学年学期）、`zc`（周次，空=全部）、`sfFD=1`（放大）、可选 `kbjcmsid`（节次模式）。格子在 `#kbtable` / `.kbcontent`。CLI：`jwxt schedule --semester <学年学期> [--week <周次>]`。 |
| 首页当日课表碎片 | `/jsxsd/framework/main_index_loadkb.jsp?rq=YYYY-MM-DD` | 由 `xsMain_new` jQuery `.load` 拉入。可选 `sjmsValue`。 |
| 考试安排查询 | `/jsxsd/xsks/xsksap_query` | GET 200 |
| 学生选课中心 | `/jsxsd/xsxk/xklc_list` | 只读看轮次。禁止进入轮次后提交选课。 |
| 选课结果查询 | `/jsxsd/xkgl/xsxkjgcx` | GET 200 |
| 教学周历 | `/jsxsd/jxzl/jxzl_query` | GET 200 |
| 培养方案及完成情况 | `/jsxsd/pyfa/topyfamx` | GET 200，页面较大 |

成绩查询 iframe 完整路径：

```text
GET /jsxsd/kscj/cjcx_query          # 查询条件框
GET /jsxsd/kscj/cjcx_list?kksj=2025-2026-3
```

课表查询（只读，等同页面点「学期」下拉）：

```text
GET /jsxsd/xskb/xskb_list.do?xnxq01id=2025-2026-3&zc=&sfFD=1&kbjcmsid=94786EE0ABE2D3B2E0531E64A8C09931
```

`kbjcmsid` 以页面当前 selected option 为准，不要写死。

`jwxt schedule` 返回 `items` 和 `schedule` 两个同内容数组；每个非空格包含 `day`（周一至周日）、`period`（原始节次文字）、`course_name`（单元格首行）、`lines`（详情行）和 `text`（详情行拼接）。未知星期列会被跳过，不会猜测日期。

### Sidebar menu (from xsMain.jsp)

Parent groups: 教学评价 / 我的申请 / 我的考试 / 成绩管理 / 培养方案 / 我的课表 / 选课管理 / 教材管理 / 辅修管理 / 实验教学 / 创新学分 / 毕业设计 / 学科竞赛 / 公告留言 / 个人信息 / 在线问答 / 教学周历 / 学籍管理 / 我的成绩 / 毕业管理 / 优秀生转专业.

Useful read-only `data-url` values (prefix `/jsxsd`):

| 标题 | data-url |
|---|---|
| 学期理论课表 | `/xskb/xskb_list.do` |
| 实验课表查询 | `/syjx/toXskb.do` |
| 班级/教师/教室/课程课表 | `/kbcx/kbxx_xzb` `/kbcx/kbxx_teacher` `/kbcx/kbxx_classroom` `/kbcx/kbxx_kc` |
| 课程成绩查询 | `/kscj/cjcx_frm` |
| 等级考试成绩 | `/kscj/djkscj_list` |
| 考试安排查询 | `/xsks/xsksap_query` |
| 选课结果查询 | `/xkgl/xsxkjgcx` |
| 学籍信息管理 | `/xsxj/xjxxgl.do` |
| 学业预警查询 | `/xsxj/xsyjxx.do` |
| 培养方案及完成情况 | `/pyfa/topyfamx` |
| 教学周历查看 | `/jxzl/jxzl_query` |

Write / 申请类（菜单有入口，skill 禁止调用提交）：学生评价 `/xspj/xspj_find.do`、缓考申请、补考报名、选课中心 `/xsxk/xklc_list`、预选管理、教材确认、辅修报名/撤销、实验预约、创新学分申报、毕业设计上传、转专业申请、修改个人信息保存、学籍修改 `toEditxsxx.do`。

## Out of scope

选课、评教、保存个人信息、提交任何业务表单。成绩和课表查询均为只读 CLI；图书馆座位尚未实现。
