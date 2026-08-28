# easy-qfnu-skill

QFNU campus-system skill for AI agents.

Current coverage:

- Public notices on [曲阜师范大学教务处](https://jwc.qfnu.edu.cn/)
- Login/session/profile for [强智教务系统](http://zhjw.qfnu.edu.cn/) (`jsxsd`)

Grades, schedule, and library seats are planned. The CLI is query-only; it will not 选课 or 评教.

## Skill layout

```text
easy-qfnu-skill/
  SKILL.md
  agents/openai.yaml
  scripts/qfnu            # unified CLI
  scripts/qfnu_jwc.py     # academic-affairs office client
  scripts/qfnu_jwxt.py    # teaching-system login client
  scripts/setup_ocr       # create ddddocr venv
  scripts/run_ocr         # start local Flask OCR
  ocr/server.py           # ddddocr HTTP service
  ocr/requirements.txt
  references/jwc.md
  references/jwxt.md
```

## Setup

教务处公告只用 Python 标准库。强智登录需要本地验证码 OCR：

```bash
python3 easy-qfnu-skill/scripts/setup_ocr
python3 easy-qfnu-skill/scripts/run_ocr --host 127.0.0.1 --port 5000
```

`setup_ocr` 在 `easy-qfnu-skill/ocr/.venv` 安装 `ddddocr` 和 `Flask`，不污染系统 Python。有 `uv` 就用 `uv`（更快，适合云虚拟机 / 本地 agent），没有或失败则回退标准库 `venv + pip`。`uv` 不是硬依赖。

强制选择安装器：

```bash
python3 easy-qfnu-skill/scripts/setup_ocr --installer uv
python3 easy-qfnu-skill/scripts/setup_ocr --installer pip
# 或 QFNU_OCR_INSTALLER=uv|pip|auto
```

服务默认 `POST http://127.0.0.1:5000/ocr`，表单字段 `image` 为验证码图片 Base64。

Environment:

| 变量 | 说明 |
|---|---|
| `QFNU_OCR_URL` | OCR 服务根地址，默认 `http://127.0.0.1:5000` |
| `QFNU_OCR_INSTALLER` | OCR 安装器：`auto`（默认）/ `uv` / `pip` |
| `QFNU_JWXT_USERNAME` | 学号（也可 `--username`） |
| `QFNU_JWXT_PASSWORD` | 教务密码（也可 `--password`） |
| `QFNU_JWXT_COOKIE_PATH` | 会话 Cookie JSON 路径，默认 `~/.local/state/easy-qfnu-skill/jwxt-session.json` |

Do not commit credentials or the session file.

## CLI

```bash
python3 easy-qfnu-skill/scripts/qfnu jwc list --channel notices --limit 10
python3 easy-qfnu-skill/scripts/qfnu jwc search "选课"
python3 easy-qfnu-skill/scripts/qfnu jwc get info/1103/7719.htm

python3 easy-qfnu-skill/scripts/qfnu jwxt login --username <学号> --password <密码>
python3 easy-qfnu-skill/scripts/qfnu jwxt status   # logged_in + profile
python3 easy-qfnu-skill/scripts/qfnu jwxt logout
```

Output is JSON. JWC needs network access to `jwc.qfnu.edu.cn`. JWXT needs network access to `zhjw.qfnu.edu.cn` plus the local OCR service.
