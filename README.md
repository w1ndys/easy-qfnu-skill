# easy-qfnu-skill (曲奇教务skill)

A QFNU campus-system skill for AI agents. It is under rapid development, so features, commands, and supported scopes may change frequently.

Current coverage:

- Public notices from the [QFNU Academic Affairs Office](https://jwc.qfnu.edu.cn/)
- Login, session, and profile for the [Qiangzhi teaching system](http://zhjw.qfnu.edu.cn/) (`jsxsd`)
- Read-only course-grade queries from the Qiangzhi teaching system
- Read-only semester-schedule queries from the Qiangzhi teaching system
- Freshman entrance-exam question-bank search through the public [fq.easy-qfnu.top](https://fq.easy-qfnu.top/) API

Library-seat queries are planned. The CLI is query-only and never performs course selection (`选课`) or teaching evaluation (`评教`).

## Skill layout

```text
easy-qfnu-skill/
  SKILL.md
  agents/openai.yaml
  scripts/qfnu            # unified CLI
  scripts/qfnu_jwc.py     # academic-affairs office client
  scripts/qfnu_jwxt.py    # teaching-system login client
  scripts/qfnu_freshman.py # freshman question-bank client
  scripts/freshman_question_service.py # question-bank business logic
  scripts/freshman_question_repository.py # question-bank API client
  scripts/jwxt_credentials_repository.py # saved teaching-system credentials
  scripts/qfnu_types.py   # shared entities and payload types
  references/jwc.md
  references/jwxt.md
```

## Setup

Academic-affairs notices use only the Python standard library. Qiangzhi login uses model vision by default and requires no extra package:

```bash
python3 easy-qfnu-skill/scripts/qfnu jwxt captcha --out /tmp/jwxt-captcha.png   # save captcha image and login session
python3 easy-qfnu-skill/scripts/qfnu jwxt login --username <student-id> --password <password> --captcha <captcha-text>
```

Before the first login submission, the CLI asks whether to save credentials after success. Only `yes` saves them. You can also pass `--save-credentials yes` or `--save-credentials no`; non-interactive environments default to no. Credentials are stored in `~/.local/state/easy-qfnu-skill/jwxt-credentials.json` with access restricted to the current user.

When model vision is unavailable, use the independent [ddddocr-vercel](https://github.com/w1ndys/ddddocr-vercel) service. Deploy that repository to Vercel and set its root URL:

```bash
export QFNU_OCR_URL="https://your-ddddocr-domain.vercel.app"
python3 easy-qfnu-skill/scripts/qfnu jwxt login --username <student-id> --password <password>
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
python3 easy-qfnu-skill/scripts/qfnu jwc list --channel notices --limit 10
python3 easy-qfnu-skill/scripts/qfnu jwc search "选课"
python3 easy-qfnu-skill/scripts/qfnu jwc get info/1103/7719.htm
python3 easy-qfnu-skill/scripts/qfnu freshman search "校规" --page-size 20

python3 easy-qfnu-skill/scripts/qfnu jwxt captcha --out /tmp/jwxt-captcha.png  # model vision or user visual reading
python3 easy-qfnu-skill/scripts/qfnu jwxt login --username <student-id> --password <password> --captcha <captcha-text>
python3 easy-qfnu-skill/scripts/qfnu jwxt login --username <student-id> --password <password>  # independent OCR when QFNU_OCR_URL is set
python3 easy-qfnu-skill/scripts/qfnu jwxt status   # logged_in and profile
python3 easy-qfnu-skill/scripts/qfnu jwxt grades --semester 2025-2026-3
python3 easy-qfnu-skill/scripts/qfnu jwxt schedule --semester 2025-2026-3 --week 1
python3 easy-qfnu-skill/scripts/qfnu jwxt login --save-credentials yes  # use arguments, environment, or saved credentials
python3 easy-qfnu-skill/scripts/qfnu jwxt logout  # clear the session; preserve credentials
python3 easy-qfnu-skill/scripts/qfnu jwxt logout --forget-credentials  # clear session and credentials
python3 easy-qfnu-skill/scripts/qfnu jwxt forget-credentials  # clear saved credentials only
```

When `jwxt status` detects an expired session, it attempts one automatic login only if saved credentials exist and `QFNU_OCR_URL` is configured. Without OCR it returns a manual captcha hint. Password errors and accounts logged in elsewhere stop immediately without repeated retries.

A captcha error means only that the current reading does not match. Run `jwxt captcha` again for a new image and session, then submit `jwxt login --captcha`; allow at most 3 consecutive attempts. Do not treat one captcha error as a password error.

`jwxt schedule` returns `items` and `schedule` arrays with weekday, period, course name, and cell details. Empty cells are omitted. Pass `--week` for one week or omit it for all weeks.

Output is JSON. JWC requires network access to `jwc.qfnu.edu.cn`; JWXT requires `zhjw.qfnu.edu.cn`; freshman search requires `fq.easy-qfnu.top`. The independent ddddocr service is needed only when the model cannot read the captcha image itself.
