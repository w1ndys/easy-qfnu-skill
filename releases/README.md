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

版本标签使用 `vYYYY.MM.DD.HHmm` 日期时间格式，例如 `v2026.08.30.1430`。请使用 `scripts/install-easy-qfnu` 显式安装，并在执行前核对发布版本和校验和。skill 的启动器不会自动下载或执行远程文件。

`manifest.json` 同时记录 `release_version`、`cli_version` 和 `skill_version`；skill 根目录的 `VERSION` 文件记录本地 skill 版本。CLI 启动时读取该清单和本地 `VERSION`；如果 CLI 或 skill 不是最新版本，会返回 `update_required: true` 并停止业务命令。
