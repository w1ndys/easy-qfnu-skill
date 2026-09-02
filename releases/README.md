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

版本标签使用 `vYYYY.MM.DD.HH` 日期时间格式，例如 `v2026.08.30.14`。请使用 `scripts/install-easy-qfnu` 显式安装，并在执行前核对发布版本和校验和。skill 的启动器不会自动下载或执行远程文件。

`manifest.json` 同时记录 `release_version`、`cli_version` 和 `skill_version`；三者均由 Release 标签自动生成，Release 标签是唯一版本来源。CLI 启动时读取该清单并校验自身版本，不再依赖 skill 目录中的本地版本文件；如果 CLI 不是最新版本，会返回 `update_required: true` 并停止业务命令。
