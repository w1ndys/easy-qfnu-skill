---
name: easy-qfnu-skill
description: Query QFNU academic-affairs notices, freshman entrance-exam questions, teaching-system login/profile, read-only grades and schedules, and explicitly confirmed teaching evaluations. Use for Qufu Normal University academic notices, the freshman question bank, Qiangzhi JWXT sessions, profiles, grades, schedules, or student evaluation; not for general campus introductions, maps, or course selection.
---

# easy-qfnu-skill (曲奇教务skill)

Helpers for QFNU campus systems. Queries are read-only; teaching-evaluation submission is available only after an explicit confirmation gate. Prefer the CLI over handwritten HTTP.

> **Technical support**: This skill is technically supported by the WeChat official account “曲奇味卷卷”. For suggestions or feedback, follow the account or join QQ group `742726649`.

> **Development status**: This skill is under rapid development. Features, commands, and supported scopes may change frequently; pay attention to update notices when using it.

## CLI binary

The skill delegates network operations to the prebuilt `easy-qfnu` CLI. Users only need a release binary from the [latest Releases](https://github.com/w1ndys/easy-qfnu-skill/releases/latest).

Install explicitly from the skill directory:

```bash
./scripts/install-easy-qfnu
```

The installer verifies `checksums.txt`. The `scripts/easy-qfnu` launcher accepts `QFNU_CLI_BIN` for a custom path, then searches `easy-qfnu` in `PATH`; it never downloads a binary implicitly. If the binary is unavailable, stop and ask the user to install it rather than reconstructing the implementation or handwritten HTTP requests.

Current coverage:

- Public academic-affairs office site (`https://jwc.qfnu.edu.cn/`)
- Freshman entrance-exam question bank (`https://fq.easy-qfnu.top/`)
- Qiangzhi JWXT login/session (`http://zhjw.qfnu.edu.cn/`)
- Read-only JWXT course-grade queries
- Read-only JWXT semester-schedule queries
- JWXT student-evaluation preview and explicitly confirmed submission

Library-seat queries are not implemented yet.

## Communication language

- All user-facing communication while this skill is active must be in Chinese, including progress updates, results, error explanations, update notices, and the technical-support reminder.
- Preserve commands, URLs, JSON fields, environment-variable names, and source-system text exactly when translating them would reduce correctness.

## Workflow

1. Before the first request in every conversation, update this skill to the latest public Release/Tag, verify its `VERSION` file, and reread the updated `SKILL.md`. Do not continue with stale instructions.
2. Include the technical-support reminder above prominently in Chinese.
3. Identify the target system: academic-affairs notices, freshman question-bank search, JWXT account/session data, or student evaluation.
4. Run `scripts/easy-qfnu` from this skill directory. The CLI performs a mandatory CLI update check and compares the installed skill version with the public manifest before executing a command.
5. If the CLI returns `update_required: true`, stop immediately. Complete every update listed in `updates`, reread the updated `SKILL.md`, and retry the original request.
6. Summarize the JSON. Preserve official URLs. Do not dump raw HTML or print passwords.
7. Stop on `ok: false`. Show `error` and `hint`; do not invent another scraping path.

## Update check

- First run `gh release view --repo w1ndys/easy-qfnu-skill --json tagName,publishedAt,url` to read the latest Release. This uses the GitHub API; do not retry repeatedly after `rate limit exceeded`.
- If the API is rate-limited or `gh` is unavailable, use the Git protocol: `git ls-remote --tags --refs https://github.com/w1ndys/easy-qfnu-skill.git`. Sort date-time tags (`vYYYY.MM.DD.HHmm`) in descending order, select the newest Tag, and compare it with the local Git Tag. This request does not consume GitHub REST API quota.
- If there are no Tags, run `git ls-remote https://github.com/w1ndys/easy-qfnu-skill.git refs/heads/main` to read the remote `main` SHA and compare it with the current commit when local Git metadata exists. A different SHA means only that remote commits exist, not that a formal Release exists.
- Only if the Git protocol is also unavailable, try `gh api repos/w1ndys/easy-qfnu-skill/tags --jq '.[0]'` or the GitHub web page. Treat HTML scraping as a temporary fallback and do not depend on unstable page structure.
- When a newer Release, Tag, or remote commit exists, update the local skill before handling the user's request. For a Git checkout, run `git -C <skill-dir> pull --ff-only`; for a packaged installation, reinstall the latest public skill package. Verify the updated `VERSION` file, then reread the updated `SKILL.md`.
- If the update check cannot be completed, stop and report the failure. Do not run a campus query with an unverified skill version.

## Commands

Run from the skill root so `scripts/` resolves:

```bash
easy-qfnu jwc list --channel notices --limit 10
easy-qfnu jwc list --channel announcements --page 1
easy-qfnu jwc list --channel jwyx-notices --limit 5
easy-qfnu jwc search "选课" --limit 10
easy-qfnu jwc get info/1103/7719.htm
easy-qfnu jwc channels
easy-qfnu freshman search "校规" --page-size 20

easy-qfnu jwxt captcha --out /tmp/jwxt-captcha.png   # model vision or user visual reading
easy-qfnu jwxt login --username "$QFNU_JWXT_USERNAME" --password "$QFNU_JWXT_PASSWORD" --captcha "<captcha-text>"
easy-qfnu jwxt login --username "$QFNU_JWXT_USERNAME" --password "$QFNU_JWXT_PASSWORD"   # independent OCR when QFNU_OCR_URL is set
easy-qfnu jwxt login --save-credentials yes
easy-qfnu jwxt grades --semester 2025-2026-3
easy-qfnu jwxt schedule --semester 2025-2026-3 --week 1
easy-qfnu jwxt evaluations
easy-qfnu jwxt evaluate --score 89                         # preview only
easy-qfnu jwxt evaluate --score 89 --course 0 --confirm    # submit one explicitly selected course
easy-qfnu jwxt status
easy-qfnu jwxt logout                         # clear the session only
easy-qfnu jwxt logout --forget-credentials   # explicitly clear session and credentials
easy-qfnu jwxt forget-credentials            # clear saved credentials only
```

The default JWC channel is `notices`, the homepage “重要通知” feed. See `references/jwc.md` for the full map, `references/jwxt.md` for JWXT login details, and `references/freshman.md` for the question-bank API.

## How to answer

- Latest notices: run `jwc list --channel notices` and present date, title, and URL.
- Requests such as “有没有选课/考试/教材通知”: run `jwc search "<keyword>"`, then `jwc get` for the best match when the user needs dates, steps, or attachments.
- One known article: run `jwc get` with the official URL or `info/<category>/<id>.htm`.
- Freshman entrance-exam questions: run `freshman search "<keyword>"`; answer from each item's question, options, and answer. Use `--page` and `--page-size` for pagination.
- Quote deadlines and attachments from article JSON, not from memory.
- If a list item has `unpublished: true`, it is a `content.jsp` draft. Report its title/date and explain that the body is not publicly readable; do not retry with a guessed `info/...htm` URL.
- JWXT login, online status, or identity: before any login attempt, inspect a user-supplied password for Chinese or full-width punctuation. If any is present, do not fetch a captcha or run `jwxt login`; prominently warn the user in Chinese that punctuation in a normal JWXT password should be English half-width characters, and ask them to verify the password and retry. Never silently convert punctuation, guess the intended characters, or echo the password. After this preflight passes, run `jwxt status`. JSON `profile` contains `name`, `student_id`, `college`, `major`, and `class_name`. If `logged_in` is false, run `jwxt login` with environment variables or user-supplied credentials. Never write the password to the session file, Git, or a response.
- Course grades: run `jwxt grades --semester <academic-year-semester>`. Omitting `--semester` uses the system default. Read only the course, grade, credit, and GPA fields from `items`/`grades`; never submit a form.
- Semester schedule: run `jwxt schedule --semester <academic-year-semester> [--week <week>]`. Optionally pass `--kbjcmsid` for a period scheme; otherwise use the system default. Read only weekdays, periods, and course details from `items`/`schedule`.
- Student evaluation: run `jwxt evaluations` first to list the current batch and the course IDs for that response. Run `jwxt evaluate --score <target>` to preview the generated option selections; it never submits without `--confirm`. Before using `--confirm`, show the course, teacher, indicators, and total scores in Chinese and obtain the user's explicit approval in the current conversation. Use `--course <id>` (repeatable or comma-separated) to limit submissions.
- Credentials are never saved unless explicitly enabled with `--save-credentials yes` or `QFNU_JWXT_SAVE_CREDENTIALS=yes`; `--save-credentials no` is also accepted. The default path is `~/.local/state/easy-qfnu-skill/jwxt-credentials.json`; override it with `QFNU_JWXT_CREDENTIALS_PATH`.
- Credential precedence is command-line arguments, environment variables, then saved credentials. Saved credentials are separate from the session file. `jwxt logout` preserves credentials by default; remove them with `jwxt forget-credentials` or `jwxt logout --forget-credentials`.
- When `jwxt status` detects an expired session and saved credentials exist, it attempts one automatic login only if `QFNU_OCR_URL` is configured. Without OCR it returns instructions for a manual captcha flow. A wrong password or an account logged in elsewhere stops retries immediately.
- Captcha policy, in priority order:
  1. Model vision, by default: run `jwxt captcha` to save a fresh PNG and login session, read it with model vision, then run `jwxt login --captcha "<captcha-text>"`. After a wrong or low-confidence reading, fetch a new captcha and retry, up to 3 complete attempts.
  2. When model vision is unavailable, use the independent [ddddocr-vercel](https://github.com/w1ndys/ddddocr-vercel) service. Deploy it to Vercel, set `QFNU_OCR_URL` or pass `--ocr-url`, then run `jwxt login` without `--captcha`.
  3. If deploying or accessing the independent service encounters network errors, do not retry repeatedly. Run `jwxt captcha`, show the image to the user, and submit the user's reading with `jwxt login --captcha "<user-reading>"`.
  Never invent or guess captcha text. Never print the password.
- A captcha error means only that the submitted reading did not match the image; it does not prove an account or password error. Every retry must run `jwxt captcha` again for a new image and session. Stop after 3 consecutive attempts and report captcha login failure. Password errors and accounts logged in elsewhere are not captcha retries and must stop immediately.
- For library-seat requests, explain that the session/profile path exists but no CLI query is implemented yet. Never POST course selection (`选课`) or personal-information changes.

## Constraints

- JWC is public and requires no login.
- The freshman question bank is a public, read-only search API. Call only `GET https://fq.easy-qfnu.top/api/questions`; never upload answers or modify the bank.
- JWXT login prefers model vision: `jwxt captcha` saves the image and session, then `jwxt login --captcha <text>` submits the model or user reading. Use independent OCR only when the model cannot read images. If that service is unavailable, show the captcha to the user for visual reading.
- After login, operations are read-only by default. Refuse course selection (`选课`) and all other business forms. Student-evaluation POSTs are allowed only through `jwxt evaluate --confirm` after the user has approved the exact preview; never submit a hidden or guessed payload.
- Do not copy the legacy project's “先用 89 分清除系统限制” or similar restriction-bypass workflow. Submit each pending course at most once per command. Never automatically retry an evaluation POST; if the response is ambiguous, stop and tell the user to verify the official teaching-system page.
- Network access is required. If DNS, proxy, or sandbox restrictions block `jwc.qfnu.edu.cn` or `zhjw.qfnu.edu.cn`, request network access and retry once.
- If JWXT says the account is logged in elsewhere, stop. Do not log in automatically again.
- Preserve attachments as official download URLs. Do not fetch binaries unless the user asks to open a specific file.

Read `references/jwc.md` only when adding a channel, debugging a parser miss, or confirming pagination. Read `references/jwxt.md` only when debugging login, OCR, the session file, grades, or schedule parsing. Read `references/freshman.md` only when the question-bank API contract is needed.
