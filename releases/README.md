# easy-qfnu CLI releases

`easy-qfnu` is the command-line binary used by this skill. This directory documents the public release boundary; platform binaries are attached to [GitHub Releases](https://github.com/w1ndys/easy-qfnu-skill/releases).

每个版本包含以下平台产物：

- `easy-qfnu-linux-amd64`
- `easy-qfnu-linux-arm64`
- `easy-qfnu-darwin-amd64`
- `easy-qfnu-darwin-arm64`
- `easy-qfnu-windows-amd64.exe`
- `checksums.txt`（SHA-256）
- `manifest.json`（Release、CLI 和 skill 三者的版本及产物校验信息）

版本标签使用 `vYYYY.MM.DD.HH` 日期时间格式，例如 `v2026.08.30.14`。请从 skill 目录执行 `scripts/install-easy-qfnu`；安装器优先通过 `gh-proxy.com` 加速镜像下载 Release 资产，镜像 URL 格式为 `https://gh-proxy.com/https://github.com/w1ndys/easy-qfnu-skill/releases/latest/download/<asset>`，镜像不可用时回退到 GitHub 官方地址，并在安装前校验 `checksums.txt`。安装器会把校验通过的 CLI 放入同目录的 `bin/`，不会安装到 `~/.local/bin` 或其他 home 目录，也不要求加入 PATH。安装后使用 `scripts/easy-qfnu` 调用。skill 的启动器不会自动下载或执行远程文件。

`manifest.json` 同时记录 `release_version`、`cli_version` 和 `skill_version`；三者均由 Release 标签自动生成，Release 标签是唯一版本来源。CLI 启动时读取该清单并校验自身版本，不再依赖 skill 目录中的本地版本文件；如果 CLI 不是最新版本，会返回 `update_required: true` 并停止业务命令。
