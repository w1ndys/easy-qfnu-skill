---
name: easy-qfnu-skill
description: Query QFNU academic-affairs office notices and teaching-system login/profile. Use for 曲阜师范大学教务处公告/通知/新闻检索，以及强智教务登录状态与学生信息（姓名/学号/院系/专业/班级）；not for generic campus intro, maps, 选课, or 评教.
---

# easy-qfnu-skill

Read-only helpers for QFNU campus systems. Prefer the CLI over handwritten HTTP.

Current coverage:

- Public academic-affairs office site (`https://jwc.qfnu.edu.cn/`)
- 强智教务 login/session (`http://zhjw.qfnu.edu.cn/`)

成绩、课表、图书馆座位 are not implemented yet.

## Workflow

1. Identify the system: 教务处公告 vs 教务系统账号会话.
2. Run `scripts/qfnu` from this skill directory. Do not reconstruct VSB pagination, 强智 captcha, or `encoded` encryption.
3. Summarize the JSON. Keep official URLs. Do not dump raw HTML or print passwords.
4. Stop on `ok: false`. Show `error` and `hint`; do not invent another scraping path.

## Commands

Run from the skill root so `scripts/` resolves:

```bash
python3 scripts/qfnu jwc list --channel notices --limit 10
python3 scripts/qfnu jwc list --channel announcements --page 1
python3 scripts/qfnu jwc list --channel jwyx-notices --limit 5
python3 scripts/qfnu jwc search "选课" --limit 10
python3 scripts/qfnu jwc get info/1103/7719.htm
python3 scripts/qfnu jwc channels

python3 scripts/qfnu jwxt captcha --out /tmp/jwxt-captcha.png   # 默认：模型识图 / 用户肉眼识图
python3 scripts/qfnu jwxt login --username "$QFNU_JWXT_USERNAME" --password "$QFNU_JWXT_PASSWORD" --captcha "识图结果"
python3 scripts/qfnu jwxt login --username "$QFNU_JWXT_USERNAME" --password "$QFNU_JWXT_PASSWORD"   # 已配置 QFNU_OCR_URL 时使用独立服务
python3 scripts/qfnu jwxt status
python3 scripts/qfnu jwxt logout
```

Default JWC channel is `notices` (首页「重要通知」). Full map: `references/jwc.md`. JWXT login details: `references/jwxt.md`.

## How to answer

- Latest notices: `jwc list --channel notices`. Table of date, title, URL.
- "有没有选课/考试/教材通知": `jwc search "<keyword>"`, then `jwc get` for the best match if the user needs dates, steps, or attachments.
- One known article: `jwc get` with the official URL or `info/<栏目>/<id>.htm`.
- Quote deadlines and attachments from the article JSON, not from memory.
- If a list item has `unpublished: true`, it is a `content.jsp` draft. Tell the user the title/date and that the body is not publicly readable; do not retry with a guessed `info/...htm` URL.
- 教务登录/是否在线/我是谁: `jwxt status`. JSON `profile` has name, student_id, college, major, class_name. If `logged_in` is false, `jwxt login` using env vars or user-supplied credentials. Never write the password into the session file, git, or the reply.
- Captcha policy, in priority order:
  1. 模型识图（默认）：run `jwxt captcha` to save a fresh captcha PNG plus the login session, read the image with model vision, then run `jwxt login --captcha "<识图结果>"`. On a wrong captcha or a low-confidence read, re-run `jwxt captcha` for a new image (at most 3 tries).
  2. 模型没有识图能力时使用独立 [ddddocr](https://github.com/w1ndys/ddddocr) 服务：把该仓库部署到 Vercel，设置 `QFNU_OCR_URL` 或传入 `--ocr-url`，然后运行不带 `--captcha` 的 `jwxt login`。
  3. 独立服务部署或访问遇到网络问题时不要自己反复重试。改用用户肉眼识图：运行 `jwxt captcha` 保存图片并展示给用户，用户把看到的字符发到对话里，agent 执行 `jwxt login --captcha "<用户读出的字符>"` 提交。
  Never invent or guess captcha text. Never print the password.
- If the user asks for 成绩/课表/图书馆座位, say the session/profile path exists and those queries are probed but not implemented as CLI commands yet. Do not POST 选课/评教/保存个人信息.

## Constraints

- JWC is public and needs no login.
- JWXT login prefers model vision by default: `jwxt captcha` saves the captcha image + session, then `jwxt login --captcha <text>` submits the model-read (or user-read) text. Use the independent ddddocr service only when the model cannot read images; set `QFNU_OCR_URL` to its deployed root URL before running plain `jwxt login`. If the service is unavailable, show the captcha image for the user to read by eye.
- Read-only after login. Refuse 选课、评教、提交表格. Allowed POSTs are JWC site search and JWXT login/captcha/OCR already wrapped by the CLI.
- Network is required. If DNS/proxy/sandbox blocks `jwc.qfnu.edu.cn` or `zhjw.qfnu.edu.cn`, request network access and retry once.
- If JWXT says the account logged in elsewhere, stop. Do not auto-relogin.
- Attachments stay as official download URLs. Do not fetch binaries unless the user asked to open a specific file.

Read `references/jwc.md` only when adding a channel, debugging a parser miss, or confirming pagination. Read `references/jwxt.md` only when debugging login, OCR, or the session file.
