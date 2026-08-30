# qfnu CLI releases

`qfnu` is a Go binary built from the private [easy-qfnu-cli](https://github.com/w1ndys/easy-qfnu-cli) repository. This directory documents the public release boundary; the actual platform binaries are attached to [GitHub Releases](https://github.com/w1ndys/easy-qfnu-skill/releases), so installing the skill never requires source access.

每个版本包含以下平台产物：

- `qfnu-linux-amd64`
- `qfnu-linux-arm64`
- `qfnu-darwin-amd64`
- `qfnu-darwin-arm64`
- `qfnu-windows-amd64.exe`
- `checksums.txt`（SHA-256）

请使用 `scripts/install-qfnu` 显式安装，并在执行前核对发布版本和校验和。skill 的启动器不会自动下载或执行远程文件。
