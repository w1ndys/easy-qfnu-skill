# easy-qfnu-skill (曲奇教务skill)

A QFNU campus-system skill for AI agents. It is under rapid development, so features, commands, and supported scopes may change frequently. Student-evaluation submission is supported only after an explicit confirmation gate.

The skill instructions are written primarily in English, but every user-facing conversation produced while the skill is active must be in Chinese. Commands, URLs, JSON fields, and source-system text remain unchanged where translation would reduce correctness.

Current coverage:

- Public notices from the [QFNU Academic Affairs Office](https://jwc.qfnu.edu.cn/)
- Login, session, and profile for the [Qiangzhi teaching system](http://zhjw.qfnu.edu.cn/) (`jsxsd`)
- Read-only course-grade queries from the Qiangzhi teaching system
- Read-only semester-schedule queries from the Qiangzhi teaching system
- Student-evaluation preview and explicitly confirmed submission through the Qiangzhi teaching system
- Freshman entrance-exam question-bank search through the public [fq.easy-qfnu.top](https://fq.easy-qfnu.top/) API

Library-seat queries are planned. The CLI is query-only except for explicitly confirmed student-evaluation submissions; it never performs course selection (`选课`).

## Skill layout

```text
easy-qfnu-skill/
  SKILL.md
  VERSION
  agents/openai.yaml
  scripts/easy-qfnu         # public launcher for the Go CLI
  scripts/install-easy-qfnu # explicit binary installer
  releases/README.md        # release and checksum policy
  references/jwc.md
  references/jwxt.md
```

## Setup

Install the prebuilt Go CLI for your platform from the [latest release](https://github.com/w1ndys/easy-qfnu-skill/releases/latest). Releases include SHA-256 checksums. On Linux or macOS, run the explicit installer from this skill directory:

```bash
./easy-qfnu-skill/scripts/install-easy-qfnu
```

The launcher does not download binaries implicitly. If `easy-qfnu` is not in `PATH`, set `QFNU_CLI_BIN` to an installed executable or run the installer above.

每次使用前先更新本 skill 到最新公开 Release/Tag，并重新读取 `easy-qfnu-skill/SKILL.md`。CLI 启动时还会读取 Release 的 `manifest.json`，同时检查 CLI 和 skill 版本；如果返回 `update_required: true`，必须完成提示中的更新后再重试。

Qiangzhi login uses model vision by default and requires no extra package:

```bash
easy-qfnu jwxt captcha --out /tmp/jwxt-captcha.png   # save captcha image and login session
easy-qfnu jwxt login --username <student-id> --password <password> --captcha <captcha-text>
```

Credentials are never saved unless explicitly enabled with `--save-credentials yes` or `QFNU_JWXT_SAVE_CREDENTIALS=yes`; `--save-credentials no` is also accepted. Credentials are stored in `~/.local/state/easy-qfnu-skill/jwxt-credentials.json` with access restricted to the current user.

When model vision is unavailable, use the independent [ddddocr-vercel](https://github.com/w1ndys/ddddocr-vercel) service. Deploy that repository to Vercel and set its root URL:

```bash
export QFNU_OCR_URL="https://your-ddddocr-domain.vercel.app"
easy-qfnu jwxt login --username <student-id> --password <password>
```

The service exposes `POST /ocr`; form or JSON field `image` accepts Base64 captcha image data. See the ddddocr repository for a complete example.

If OCR deployment or access encounters network errors, do not retry automatically. Run `jwxt captcha`, show the image to the user, and submit the user's reading with `jwxt login --captcha "<user-reading>"`.

Environment variables:

| Variable | Description |
|---|---|
| `QFNU_OCR_URL` | Independent ddddocr service root URL; without it, login requires `--captcha` |
| `QFNU_JWXT_USERNAME` | Student ID; alternative to `--username` |
| `QFNU_JWXT_PASSWORD` | Teaching-system password; alternative to `--password` |
| `QFNU_JWXT_COOKIE_PATH` | Session Cookie JSON path; default `~/.local/state/easy-qfnu-skill/jwxt-session.json` |
| `QFNU_JWXT_CREDENTIALS_PATH` | Automatic-login credential path; default `~/.local/state/easy-qfnu-skill/jwxt-credentials.json` |
| `QFNU_JWXT_SAVE_CREDENTIALS` | Non-interactive save choice; only `yes` saves credentials |

Never commit credentials or the session file.

## CLI

```bash
easy-qfnu jwc list --channel notices --limit 10
easy-qfnu jwc search "选课"
easy-qfnu jwc get info/1103/7719.htm
easy-qfnu freshman search "校规" --page-size 20

easy-qfnu jwxt captcha --out /tmp/jwxt-captcha.png  # model vision or user visual reading
easy-qfnu jwxt login --username <student-id> --password <password> --captcha <captcha-text>
easy-qfnu jwxt login --username <student-id> --password <password>  # independent OCR when QFNU_OCR_URL is set
easy-qfnu jwxt status   # logged_in and profile
easy-qfnu jwxt grades --semester 2025-2026-3
easy-qfnu jwxt schedule --semester 2025-2026-3 --week 1
easy-qfnu jwxt evaluations
easy-qfnu jwxt evaluate --score 89                         # preview only
easy-qfnu jwxt evaluate --score 89 --course 0 --confirm    # submit after explicit approval
easy-qfnu jwxt login --save-credentials yes  # use arguments, environment, or saved credentials
easy-qfnu jwxt logout  # clear the session; preserve credentials
easy-qfnu jwxt logout --forget-credentials  # clear session and credentials
easy-qfnu jwxt forget-credentials  # clear saved credentials only
```

When `jwxt status` detects an expired session, it attempts one automatic login only if saved credentials exist and `QFNU_OCR_URL` is configured. Without OCR it returns a manual captcha hint. Password errors and accounts logged in elsewhere stop immediately without repeated retries.

A captcha error means only that the current reading does not match. Run `jwxt captcha` again for a new image and session, then submit `jwxt login --captcha`; allow at most 3 consecutive attempts. Do not treat one captcha error as a password error.

`jwxt evaluations` lists the current evaluation batch. `jwxt evaluate --score <target>` builds a preview for every pending course and performs no POST by default. After reviewing course names, teachers, indicators, and totals with the user, rerun the same command with `--confirm` to submit. Use `--course <id>` to limit the batch. A target of 100 may trigger the system's option restriction; 98 or lower is recommended. The command never uses the legacy restriction-bypass flow and never retries an ambiguous submission.

`jwxt schedule` returns `items` and `schedule` arrays with weekday, period, course name, and cell details. Empty cells are omitted. Pass `--week` for one week or omit it for all weeks.

Output is JSON. JWC requires network access to `jwc.qfnu.edu.cn`; JWXT requires `zhjw.qfnu.edu.cn`; freshman search requires `fq.easy-qfnu.top`. The independent ddddocr service is needed only when the model cannot read the captcha image itself.
