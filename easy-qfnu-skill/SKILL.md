---
name: easy-qfnu-skill
description: Query QFNU academic-affairs office notices, freshman entrance-exam questions, teaching-system login/profile, and read-only course grades. Use for 曲阜师范大学教务处公告/通知/新闻检索、新生入学考试题库搜索、强智教务登录状态与学生信息（姓名/学号/院系/专业/班级）及课程成绩查询；not for generic campus intro, maps, 选课, or 评教.
---

# 曲奇教务skill（easy-qfnu-skill）

Read-only helpers for QFNU campus systems. Prefer the CLI over handwritten HTTP.

Current coverage:

- Public academic-affairs office site (`https://jwc.qfnu.edu.cn/`)
- 新生入学考试题库 (`https://fq.easy-qfnu.top/`)
- 强智教务 login/session (`http://zhjw.qfnu.edu.cn/`)
- 强智教务课程成绩查询（只读）

课表、图书馆座位 are not implemented yet.

## Workflow

1. On the first use in every conversation, check whether this skill has a newer GitHub Release or Tag. Follow the update-check rules below; do not repeat the check for later messages in the same conversation.
2. Identify the system: 教务处公告 vs 新生题库搜索 vs 教务系统账号会话.
3. Run `scripts/qfnu` from this skill directory. Do not reconstruct VSB pagination, 强智 captcha, or `encoded` encryption.
4. Summarize the JSON. Keep official URLs. Do not dump raw HTML or print passwords.
5. Stop on `ok: false`. Show `error` and `hint`; do not invent another scraping path.

## Update check

- First run `gh release view --repo w1ndys/easy-qfnu-skill --json tagName,publishedAt,url` to read the latest published Release.
- If the repository has no Release, run `gh api repos/w1ndys/easy-qfnu-skill/tags --jq '.[0]'` and use the latest Tag instead.
- Compare the latest Release/Tag with the local Git tag or commit when repository metadata is available. Only tell the user when an update exists; ask before pulling, installing, or replacing files.
- If no Release or Tag exists, treat the repository as having no published version and continue the user's task.
- If GitHub is unavailable, mention once that the update check could not be completed, then continue the user's task. Never block the requested campus query on this check.

## Commands

Run from the skill root so `scripts/` resolves:

```bash
python3 scripts/qfnu jwc list --channel notices --limit 10
python3 scripts/qfnu jwc list --channel announcements --page 1
python3 scripts/qfnu jwc list --channel jwyx-notices --limit 5
python3 scripts/qfnu jwc search "选课" --limit 10
python3 scripts/qfnu jwc get info/1103/7719.htm
python3 scripts/qfnu jwc channels
python3 scripts/qfnu freshman search "校规" --page-size 20

python3 scripts/qfnu jwxt captcha --out /tmp/jwxt-captcha.png   # 默认：模型识图 / 用户肉眼识图
python3 scripts/qfnu jwxt login --username "$QFNU_JWXT_USERNAME" --password "$QFNU_JWXT_PASSWORD" --captcha "识图结果"
python3 scripts/qfnu jwxt login --username "$QFNU_JWXT_USERNAME" --password "$QFNU_JWXT_PASSWORD"   # 已配置 QFNU_OCR_URL 时使用独立服务
python3 scripts/qfnu jwxt login --save-credentials yes
python3 scripts/qfnu jwxt grades --semester 2025-2026-3
python3 scripts/qfnu jwxt status
python3 scripts/qfnu jwxt logout                         # 默认只清除会话
python3 scripts/qfnu jwxt logout --forget-credentials   # 明确同时清除凭据
python3 scripts/qfnu jwxt forget-credentials            # 只清除凭据
```

Default JWC channel is `notices` (首页「重要通知」). Full map: `references/jwc.md`. JWXT login details: `references/jwxt.md`.
Freshman question-bank API details: `references/freshman.md`.

## How to answer

- Latest notices: `jwc list --channel notices`. Table of date, title, URL.
- "有没有选课/考试/教材通知": `jwc search "<keyword>"`, then `jwc get` for the best match if the user needs dates, steps, or attachments.
- One known article: `jwc get` with the official URL or `info/<栏目>/<id>.htm`.
- 新生入学考试题目：运行 `freshman search "<关键词>"`，根据 `items` 中的题干、选项和答案回答；需要翻页时传入 `--page` 和 `--page-size`。
- Quote deadlines and attachments from the article JSON, not from memory.
- If a list item has `unpublished: true`, it is a `content.jsp` draft. Tell the user the title/date and that the body is not publicly readable; do not retry with a guessed `info/...htm` URL.
- 教务登录/是否在线/我是谁: `jwxt status`. JSON `profile` has name, student_id, college, major, class_name. If `logged_in` is false, `jwxt login` using env vars or user-supplied credentials. Never write the password into the session file, git, or the reply.
- 课程成绩：运行 `jwxt grades --semester <学年学期>`；省略 `--semester` 时使用教务系统默认结果。只读解析 `items`/`grades` 中的课程、成绩、学分和绩点字段，不提交任何表单。
- 首次登录时（提交前）会询问是否在成功后保存账号密码；只有输入 `yes` 或显式传入 `--save-credentials yes` 才保存。凭据默认在 `~/.local/state/easy-qfnu-skill/jwxt-credentials.json`，可用 `QFNU_JWXT_CREDENTIALS_PATH` 覆盖。
- 登录凭据优先级为命令行参数、环境变量、已保存凭据。保存凭据不会改变会话文件，`jwxt logout` 默认保留凭据；需要删除时使用 `jwxt forget-credentials` 或 `jwxt logout --forget-credentials`。
- `jwxt status` 发现会话过期且存在保存凭据时，只有配置 `QFNU_OCR_URL` 才会自动重登一次；未配置 OCR 会返回手动验证码流程提示。密码错误或账号被顶下线会停止重试。
- Captcha policy, in priority order:
  1. 模型识图（默认）：run `jwxt captcha` to save a fresh captcha PNG plus the login session, read the image with model vision, then run `jwxt login --captcha "<识图结果>"`. On a wrong captcha or a low-confidence read, re-run `jwxt captcha` for a new image (at most 3 tries).
  2. 模型没有识图能力时使用独立 [ddddocr-vercel](https://github.com/w1ndys/ddddocr-vercel) 服务：把该仓库部署到 Vercel，设置 `QFNU_OCR_URL` 或传入 `--ocr-url`，然后运行不带 `--captcha` 的 `jwxt login`。
  3. 独立服务部署或访问遇到网络问题时不要自己反复重试。改用用户肉眼识图：运行 `jwxt captcha` 保存图片并展示给用户，用户把看到的字符发到对话里，agent 执行 `jwxt login --captcha "<用户读出的字符>"` 提交。
  Never invent or guess captcha text. Never print the password.
- 验证码错误只代表本次识别结果与图片不匹配，不要据此判断账号或密码错误。每次重试都必须重新运行 `jwxt captcha` 获取新图片和会话，再用新的识别结果登录；连续最多尝试 3 次，达到上限后再报告验证码登录失败。密码错误或账号被顶下线不属于验证码重试范围，应立即停止。
- If the user asks for 课表/图书馆座位, say the session/profile path exists and those queries are probed but not implemented as CLI commands yet. Do not POST 选课/评教/保存个人信息.

## Constraints

- JWC is public and needs no login.
- 新生题库是公开只读搜索接口，不需要登录；只调用 `GET https://fq.easy-qfnu.top/api/questions`，不上传答案或修改题库。
- JWXT login prefers model vision by default: `jwxt captcha` saves the captcha image + session, then `jwxt login --captcha <text>` submits the model-read (or user-read) text. Use the independent ddddocr service only when the model cannot read images; set `QFNU_OCR_URL` to its deployed root URL before running plain `jwxt login`. If the service is unavailable, show the captcha image for the user to read by eye.
- Read-only after login. Refuse 选课、评教、提交表格. Allowed POSTs are JWC site search and JWXT login/captcha/OCR already wrapped by the CLI.
- Network is required. If DNS/proxy/sandbox blocks `jwc.qfnu.edu.cn` or `zhjw.qfnu.edu.cn`, request network access and retry once.
- If JWXT says the account logged in elsewhere, stop. Do not auto-relogin.
- Attachments stay as official download URLs. Do not fetch binaries unless the user asked to open a specific file.

Read `references/jwc.md` only when adding a channel, debugging a parser miss, or confirming pagination. Read `references/jwxt.md` only when debugging login, OCR, or the session file.
