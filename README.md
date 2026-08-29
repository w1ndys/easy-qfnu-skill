# 曲奇教务skill（easy-qfnu-skill）

QFNU campus-system skill for AI agents.

Current coverage:

- Public notices on [曲阜师范大学教务处](https://jwc.qfnu.edu.cn/)
- Login/session/profile for [强智教务系统](http://zhjw.qfnu.edu.cn/) (`jsxsd`)
- 新生入学考试题库搜索（调用 [fq.easy-qfnu.top](https://fq.easy-qfnu.top/) 公开 API）

Grades, schedule, and library seats are planned. The CLI is query-only; it will not 选课 or 评教.

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

教务处公告只用 Python 标准库。强智登录默认走模型识图，不需要安装任何东西：

```bash
python3 easy-qfnu-skill/scripts/qfnu jwxt captcha --out /tmp/jwxt-captcha.png   # 保存验证码图片 + 登录会话
python3 easy-qfnu-skill/scripts/qfnu jwxt login --username <学号> --password <密码> --captcha <识图结果>
```

首次登录时（提交前），命令行会询问是否在成功后保存账号密码；只有输入 `yes` 才会保存。也可以明确传入 `--save-credentials yes` 或 `--save-credentials no`（非交互环境默认不保存）。凭据保存在 `~/.local/state/easy-qfnu-skill/jwxt-credentials.json`，仅当前用户可读写。

模型没有识图能力时，使用独立的 [ddddocr-vercel](https://github.com/w1ndys/ddddocr-vercel) 服务。把该仓库导入 Vercel 部署，然后设置服务根地址：

```bash
export QFNU_OCR_URL="https://你的-ddddocr-域名.vercel.app"
python3 easy-qfnu-skill/scripts/qfnu jwxt login --username <学号> --password <密码>
```

服务接口为 `POST /ocr`，表单或 JSON 字段 `image` 接收验证码图片 Base64；完整调用示例见 ddddocr 仓库首页。

部署 OCR 遇到网络问题时不要自动反复重试：改用 `jwxt captcha` 保存验证码图片并展示给用户，用户把读到的字符发到对话里，再执行 `jwxt login --captcha "<用户读出的字符>"` 提交登录。

Environment:

| 变量 | 说明 |
|---|---|
| `QFNU_OCR_URL` | 独立 ddddocr 服务根地址；未配置时只能使用 `--captcha` |
| `QFNU_JWXT_USERNAME` | 学号（也可 `--username`） |
| `QFNU_JWXT_PASSWORD` | 教务密码（也可 `--password`） |
| `QFNU_JWXT_COOKIE_PATH` | 会话 Cookie JSON 路径，默认 `~/.local/state/easy-qfnu-skill/jwxt-session.json` |
| `QFNU_JWXT_CREDENTIALS_PATH` | 自动登录凭据路径，默认 `~/.local/state/easy-qfnu-skill/jwxt-credentials.json` |
| `QFNU_JWXT_SAVE_CREDENTIALS` | 非交互登录的保存选择，只有值为 `yes` 才保存 |

Do not commit credentials or the session file.

## CLI

```bash
python3 easy-qfnu-skill/scripts/qfnu jwc list --channel notices --limit 10
python3 easy-qfnu-skill/scripts/qfnu jwc search "选课"
python3 easy-qfnu-skill/scripts/qfnu jwc get info/1103/7719.htm
python3 easy-qfnu-skill/scripts/qfnu freshman search "校规" --page-size 20

python3 easy-qfnu-skill/scripts/qfnu jwxt captcha --out /tmp/jwxt-captcha.png  # 默认：模型识图 / 用户肉眼识图
python3 easy-qfnu-skill/scripts/qfnu jwxt login --username <学号> --password <密码> --captcha <识图结果>
python3 easy-qfnu-skill/scripts/qfnu jwxt login --username <学号> --password <密码>  # 已配置 QFNU_OCR_URL 时使用独立服务
python3 easy-qfnu-skill/scripts/qfnu jwxt status   # logged_in + profile
python3 easy-qfnu-skill/scripts/qfnu jwxt login --save-credentials yes  # 使用参数或环境变量/已保存凭据登录
python3 easy-qfnu-skill/scripts/qfnu jwxt logout  # 只清除会话，默认保留凭据
python3 easy-qfnu-skill/scripts/qfnu jwxt logout --forget-credentials  # 同时清除会话和凭据
python3 easy-qfnu-skill/scripts/qfnu jwxt forget-credentials  # 只清除已保存凭据
```

`jwxt status` 发现会话过期时，会在存在已保存凭据且配置 `QFNU_OCR_URL` 的情况下自动重登一次。未配置 OCR 时会返回手动验证码提示；密码错误或账号被顶下线会立即停止，不会循环重试。

验证码错误只表示本次识别结果不匹配：重新运行 `jwxt captcha` 获取新图片和会话，再提交 `jwxt login --captcha`，最多连续尝试 3 次。不要把单次验证码错误误判为密码错误。

Output is JSON. JWC needs network access to `jwc.qfnu.edu.cn`; JWXT needs network access to `zhjw.qfnu.edu.cn`; freshman search needs network access to `fq.easy-qfnu.top`. The independent ddddocr service is only needed when the model cannot read the captcha image itself.
