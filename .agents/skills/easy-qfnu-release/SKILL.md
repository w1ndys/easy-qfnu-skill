---
name: easy-qfnu-release
description: Publish an easy-qfnu date-tagged GitHub Release from the local Python skill repository with the authenticated GitHub CLI, without GitHub Actions or platform binaries.
---

# easy-qfnu 发布

使用此 skill 将当前 skill 仓库打成日期版本并发布到公共 GitHub Release。它适用于用户明确要求发布新版本、重新发布当前小时版本或检查发布文案；不用于普通 CLI 开发或 GitHub Actions 配置。

## 约定

- 版本标签使用 `vYYYY.MM.DD.HH`，默认采用执行发布命令机器的本地时间，例如 `v2026.08.30.14`。
- 此 skill 随公开 skill 仓库维护，源码仓库默认由脚本所在位置自动定位，可用 `EASY_QFNU_SKILL_REPO` 或 `--repo` 覆盖。
- 目标 Release 仓库默认为 `w1ndys/easy-qfnu-skill`，可用 `EASY_QFNU_PUBLIC_REPO` 或 `--public-repo` 覆盖。
- 不再构建或上传平台二进制、`checksums.txt` 或 `manifest.json`。GitHub 会按标签附带源码归档。
- `VERSION` 文件写入与标签相同的版本号，并随发布提交。
- Release 标题固定为版本号本身，例如 `v2026.08.30.17`，不添加产品名或括号中的版本信息。
- Release 正文固定包含“发布说明、功能更新、修复问题、改进与维护、安装、版本信息”六个章节。脚本会读取上一个公开 Release 到当前 HEAD 的提交，并按 Conventional Commit 类型生成中文用户更新点；发布流程、CI 和测试提交会过滤掉。

## 发布流程

1. 在 skill 仓库根目录先执行默认 dry-run。脚本会检查本地仓库是否干净、验证 `gh` 登录、运行测试，并打印完整 Release 文案；此阶段不改 `VERSION`、不创建标签、不推送、不上传 Release。

   ```bash
   python3 .agents/skills/easy-qfnu-release/scripts/publish_release.py
   ```

2. 检查 dry-run 输出的上一个 Release、变更范围和完整 Release 文案。确认功能更新点确实面向用户、没有泄露内部信息；必要时将人工修订后的固定格式文案写入文件，通过 `--notes-file` 传入。获得本次发布的明确确认后，才加 `--publish`：

   ```bash
   python3 .agents/skills/easy-qfnu-release/scripts/publish_release.py --publish
   ```

   自定义文案示例：

   ```bash
   python3 .agents/skills/easy-qfnu-release/scripts/publish_release.py \
     --notes-file /path/to/release-notes.md --publish
   ```

3. 同一小时已有版本时，默认停止并要求判断。只有用户明确要求覆盖当前 Release 时才使用 `--replace`。该选项会把同名标签指到当前 HEAD，然后删除并重建 GitHub Release：

   ```bash
   python3 .agents/skills/easy-qfnu-release/scripts/publish_release.py --publish --replace
   ```

脚本使用本机 `gh` 的登录身份完成 GitHub 操作，不读取或打印 Token，也不依赖 GitHub Actions。`--publish` 会把 `VERSION` 写成目标标签并提交，再打标签、推送并创建 Release。

不要在没有用户明确确认的情况下运行 `--publish` 或 `--replace`。如果仓库有未提交修改、版本格式不合法、测试失败或 GitHub 权限不足，应停止并报告具体错误。重新发布历史版本时，必须明确指定对应的 `--version`。
