#!/usr/bin/env python3
"""Publish an easy-qfnu SemVer-tagged GitHub Release from the Python skill repo."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# 版本号本体是 X.Y.Z，不带 v 前缀；标签名是它前面加 v。
SEMVER_RE = re.compile(r"^[0-9]+[.][0-9]+[.][0-9]+$")
TAG_RE = re.compile(r"^v[0-9]+[.][0-9]+[.][0-9]+$")
COMMIT_RE = re.compile(
    r"^(?P<type>[a-z]+)(?:[(](?P<scope>[^)]*)[)])?(?:!)?:[ \t]*(?P<subject>.+)$",
    re.IGNORECASE,
)
# 迁移到 X.Y.Z 之前用过日期式标签，历史 Release 仍在认它，否则会被当成不存在。
DATE_TAG_RE = re.compile(r"^v[0-9]{4}[.][0-9]{2}[.][0-9]{2}[.](?:[0-9]{2}|[0-9]{4})$")

FEATURE_TYPES = {"feat", "feature"}
FIX_TYPES = {"fix", "bugfix", "perf"}


class ReleaseError(RuntimeError):
    """A release precondition or publication step failed."""


def is_release_tag(tag):
    """判断标签是不是发布标签：X.Y.Z 与迁移前的日期式都算。"""
    return bool(TAG_RE.fullmatch(tag) or DATE_TAG_RE.fullmatch(tag))


def default_skill_repo():
    for parent in Path(__file__).resolve().parents:
        if (parent / ".git").exists() and (parent / "SKILL.md").is_file():
            return parent
    return Path.cwd()


def command_text(cmd, cwd=None, env=None):
    result = subprocess.run(cmd, cwd=cwd, env=env, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        raise ReleaseError("命令失败: " + " ".join(cmd) + "\n" + details)
    return result.stdout.strip()


def command(cmd, cwd=None, env=None):
    result = subprocess.run(cmd, cwd=cwd, env=env, check=False)
    if result.returncode != 0:
        raise ReleaseError("命令失败（退出码 " + str(result.returncode) + "）: " + " ".join(cmd))


def release_exists(public_repo, tag):
    result = subprocess.run(
        ["gh", "release", "view", tag, "--repo", public_repo],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        return True
    error = (result.stderr or result.stdout).lower()
    if "release not found" in error or "not found" in error:
        return False
    raise ReleaseError("无法检查公共 Release " + tag + ": " + (result.stderr or result.stdout).strip())


def previous_release_tag(public_repo, current_tag):
    result = subprocess.run(
        [
            "gh",
            "release",
            "list",
            "--repo",
            public_repo,
            "--limit",
            "100",
            "--json",
            "tagName,publishedAt,isDraft,isPrerelease",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise ReleaseError("无法读取历史 Release: " + (result.stderr or result.stdout).strip())
    try:
        releases = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise ReleaseError("gh 返回的 Release 列表不是有效 JSON") from exc
    published = []
    for release in releases:
        tag = release.get("tagName")
        # 只认发布标签：X.Y.Z 与迁移前的日期式，其他标签不算上一个版本。
        if not tag or not is_release_tag(tag):
            continue
        # 当前这个标签本身要跳过，否则变更范围会退化成空区间。
        if tag == current_tag:
            continue
        if release.get("isDraft") or release.get("isPrerelease"):
            continue
        if not release.get("publishedAt"):
            continue
        published.append(release)
    # 按发布时间排序而不是按版本号排序：迁移前后两种标签形态混在一起时只有时间可比。
    published.sort(key=lambda item: item["publishedAt"], reverse=True)
    if not published:
        return None
    return published[0]["tagName"]


def git_subjects(repo, previous_tag):
    if previous_tag:
        reference = previous_tag + "..HEAD"
        available = subprocess.run(
            ["git", "rev-parse", "--verify", previous_tag],
            cwd=repo,
            text=True,
            capture_output=True,
            check=False,
        ).returncode == 0
        if not available:
            return []
    else:
        reference = "HEAD"
    result = subprocess.run(
        ["git", "log", "--format=%s", "--no-merges", reference],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    subjects = []
    for line in result.stdout.splitlines():
        text = line.strip()
        if text:
            subjects.append(text)
    return subjects


def classify_subject(subject):
    match = COMMIT_RE.match(subject)
    if not match:
        return "maintenance", subject
    commit_type = match.group("type").lower()
    scope = (match.group("scope") or "").lower()
    if scope in {"release", "ci", "docs", "test", "chore", "build"}:
        return "maintenance", match.group("subject").strip()
    if commit_type in FEATURE_TYPES:
        return "features", match.group("subject").strip()
    if commit_type in FIX_TYPES:
        return "fixes", match.group("subject").strip()
    return "maintenance", match.group("subject").strip()


def public_subject(subject):
    subject = subject.replace("github.com/w1ndys/easy-qfnu-cli", "源码仓库")
    subject = subject.replace("easy-qfnu-cli", "源码仓库")
    return subject.rstrip("。．")


def release_notes(tag, previous_tag, repo, notes_file):
    if notes_file:
        notes = notes_file.read_text(encoding="utf-8").strip()
        if not notes:
            raise ReleaseError("Release 文案文件为空: " + str(notes_file))
        return notes + "\n"

    grouped = {"features": [], "fixes": [], "maintenance": []}
    seen = set()
    for raw_subject in git_subjects(repo, previous_tag):
        match = COMMIT_RE.match(raw_subject)
        scope = (match.group("scope") or "").lower() if match else ""
        if scope in {"release", "ci", "test", "chore"}:
            continue
        category, subject = classify_subject(raw_subject)
        subject = public_subject(subject)
        if not subject or subject in seen or raw_subject.lower().startswith("initial commit"):
            continue
        seen.add(subject)
        grouped[category].append(subject)

    def bullets(category, empty):
        values = grouped[category]
        if not values:
            return ["- " + empty]
        lines = []
        for value in values:
            lines.append("- " + value)
        return lines

    if previous_tag:
        change_range = "`" + previous_tag + "` → `" + tag + "`"
    else:
        change_range = "首次发布"
    lines = [
        "## 🚀 发布说明",
        "本版本变更范围：" + change_range + "。",
        "",
        "## ✨ 功能更新",
        *bullets("features", "本版本无新增功能。"),
        "",
        "## 🐛 修复问题",
        *bullets("fixes", "本版本无问题修复。"),
        "",
        "## 🔧 改进与维护",
        *bullets("maintenance", "本版本无额外改进。"),
        "",
        "## 📦 安装",
        "- 将本 skill 更新到本 Release 对应 Tag。",
        "- 从 skill 目录运行 `./scripts/easy-qfnu`；需要 Python 3，无需下载二进制或加入 PATH。",
        "",
        "## 🔐 版本信息",
        "| 项目 | 版本 |",
        "| --- | --- |",
        "| Release | `" + tag + "` |",
        "| CLI | `" + tag + "` |",
        "| skill | `" + tag + "` |",
        "",
        "本地 `VERSION` 文件写版本号本体（不带 `v` 前缀），与 Release 标签一致。使用前请先更新到最新版本。",
    ]
    return "\n".join(lines) + "\n"


def remote_tag_exists(repo, tag):
    result = subprocess.run(
        ["git", "ls-remote", "--exit-code", "--tags", "origin", "refs/tags/" + tag],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        return True
    if result.returncode == 2:
        return False
    raise ReleaseError("无法检查远端标签: " + (result.stderr or result.stdout).strip())


def local_tag_exists(repo, tag):
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "refs/tags/" + tag],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def tagged_commit(repo, tag):
    return command_text(["git", "rev-parse", tag + "^{commit}"], cwd=repo)


def push_annotated_tag(repo, tag, message):
    local_tag = local_tag_exists(repo, tag)
    remote_tag = remote_tag_exists(repo, tag)
    tag_args = ["git", "tag", "-a", tag, "-m", message]
    if local_tag:
        tag_args.insert(2, "-f")
    command(tag_args, cwd=repo)
    push_args = ["git", "push", "origin", tag]
    if remote_tag:
        push_args.insert(2, "--force")
    command(push_args, cwd=repo)


def confirm_tag_at_head(repo, tag):
    head = command_text(["git", "rev-parse", "HEAD"], cwd=repo)
    if tagged_commit(repo, tag) != head:
        raise ReleaseError(str(repo) + " 的标签 " + tag + " 没有指到当前 HEAD")


def porcelain_paths(repo):
    # 这里不能用 command_text：它会把整段输出 strip 掉，而 porcelain 每行以状态位开头
    # （未暂存修改是「 M 路径」），前导空格一去 line[3:] 就会把路径名切掉第一个字符。
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise ReleaseError("无法读取仓库状态: " + (result.stderr or result.stdout).strip())
    paths = []
    for line in result.stdout.splitlines():
        # 空行不是状态行，跳过。
        if not line.strip():
            continue
        # porcelain 的一行是「两位状态 + 一个空格 + 路径」，从第 4 个字符起才是路径。
        body = line[3:]
        # 重命名行写作「旧 -> 新」，只关心最终路径。
        if " -> " in body:
            body = body.split(" -> ", 1)[1]
        paths.append(body)
    return paths


def assert_clean(repo, allowed=None):
    if allowed is None:
        allowed = set()
    extra = []
    for path in porcelain_paths(repo):
        if path not in allowed:
            extra.append(path)
    if extra:
        raise ReleaseError("仓库有未提交修改；请先提交或清理工作树: " + ", ".join(extra))


def read_version(repo):
    path = repo / "VERSION"
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return text


def write_version(repo, version):
    # VERSION 文件只写版本号本体，不带 v 前缀，CLI 与用户直接引用它。
    (repo / "VERSION").write_text(version + "\n", encoding="utf-8")


def commit_version(repo, version):
    command(["git", "add", "VERSION"], cwd=repo)
    command(["git", "commit", "-m", "chore(release): 发布 easy-qfnu " + version], cwd=repo)
    command(["git", "push", "origin", "HEAD"], cwd=repo)


def parse_version(text):
    """把 X.Y.Z 拆成三个整数；不是这个形式时返回 None。"""
    if not SEMVER_RE.fullmatch(str(text or "")):
        return None
    parts = str(text).split(".")
    return int(parts[0]), int(parts[1]), int(parts[2])


def bump_version(text, level):
    """按 patch/minor/major 递增版本号，返回 (新版本, 失败说明)。"""
    parts = parse_version(text)
    # 读不出三段数字时不猜，交给调用方去要一个显式版本号。
    if parts is None:
        return None, "当前 VERSION 不是 X.Y.Z 形式，无法递增: " + str(text)
    major, minor, patch = parts
    # 主版本递增清空后两位：下游可以只按主版本号判断兼容性。
    if level == "major":
        return str(major + 1) + ".0.0", None
    # 次版本递增清空补丁位：新增能力不影响已有用法。
    if level == "minor":
        return str(major) + "." + str(minor + 1) + ".0", None
    # 剩下只可能是 patch：修复与内部改动。
    return str(major) + "." + str(minor) + "." + str(patch + 1), None


def resolve_version(repo, args):
    """定下本次发布的版本号，返回 (版本号, 失败说明)。

    --version 与 --bump 必须二选一：迁移到 X.Y.Z 的第一版或重新发布历史版本时直接给版本号，
    平常发布给递增级别，由脚本从 VERSION 文件推出下一个版本。
    """
    # 两个都给说明用户没想清要发哪个版本，直接拒绝而不是猜一个。
    if args.version and args.bump:
        return None, "--version 与 --bump 只能给一个"
    # 显式版本号：迁移首版与重发历史版本走这条。
    if args.version:
        # 版本号本体不带 v 前缀，带 v 的写法属于标签名，直接拒绝。
        if parse_version(args.version) is None:
            return None, "版本号必须是 X.Y.Z 形式（不带 v 前缀）: " + str(args.version)
        return args.version, None
    # 没声明递增级别时不猜，避免把不兼容变更静默发成补丁版本。
    if not args.bump:
        return None, "必须用 --bump patch|minor|major 声明递增级别，或用 --version 直接指定版本号"
    next_version, error = bump_version(read_version(repo), args.bump)
    if error is not None:
        return None, error + "；迁移到 X.Y.Z 的第一版请用 --version 指定，例如 --version 1.0.0"
    return next_version, None


def validate_repo(repo):
    if not (repo / ".git").exists():
        raise ReleaseError("不是 Git 仓库: " + str(repo))
    if not (repo / "SKILL.md").is_file():
        raise ReleaseError("找不到 SKILL.md: " + str(repo / "SKILL.md"))
    if not (repo / "python" / "qfnu" / "cli.py").is_file():
        raise ReleaseError("找不到 Python CLI: " + str(repo / "python" / "qfnu" / "cli.py"))
    if not (repo / "scripts" / "easy-qfnu").is_file():
        raise ReleaseError("找不到启动器: " + str(repo / "scripts" / "easy-qfnu"))


def run_checks(repo):
    ruff = shutil.which("ruff")
    if ruff:
        command([ruff, "check", "python", "scripts/easy-qfnu"], cwd=repo)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo / "python")
    command([sys.executable, "-m", "unittest", "discover", "-s", "python/tests"], cwd=repo, env=env)


def publish_github_release(public_repo, tag, notes, existing_release):
    if existing_release:
        command(["gh", "release", "delete", tag, "--repo", public_repo, "--yes"])
    command(
        [
            "gh",
            "release",
            "create",
            tag,
            "--repo",
            public_repo,
            "--title",
            tag,
            "--notes",
            notes,
        ]
    )


def parse_args():
    parser = argparse.ArgumentParser(description="Publish easy-qfnu SemVer releases")
    parser.add_argument("--repo", type=Path, default=None, help="local skill repository")
    parser.add_argument("--public-repo", default=None, help="public release repository")
    parser.add_argument(
        "--bump",
        choices=("patch", "minor", "major"),
        default=None,
        help="从 VERSION 递增版本号；与 --version 二选一",
    )
    parser.add_argument("--version", default=None, help="直接指定 X.Y.Z；与 --bump 二选一")
    parser.add_argument("--notes-file", type=Path, default=None, help="可选的 Release 文案文件")
    parser.add_argument("--publish", action="store_true", help="write VERSION, create/push tag and upload release")
    parser.add_argument("--replace", action="store_true", help="replace an existing tag/release")
    return parser.parse_args()


def main():
    args = parse_args()
    default_repo = default_skill_repo()
    repo = (args.repo or Path(os.environ.get("EASY_QFNU_SKILL_REPO", default_repo))).expanduser().resolve()
    public_repo = args.public_repo or os.environ.get("EASY_QFNU_PUBLIC_REPO", "w1ndys/easy-qfnu-skill")
    if args.replace and not args.publish:
        raise ReleaseError("--replace 只能与 --publish 一起使用")
    if args.notes_file and not args.notes_file.is_file():
        raise ReleaseError("找不到 Release 文案文件: " + str(args.notes_file))

    validate_repo(repo)
    version, version_error = resolve_version(repo, args)
    # 版本号定不下来时不进入后续检查，避免打出一个猜出来的标签。
    if version_error is not None:
        raise ReleaseError(version_error)
    # 标签名是版本号本体加 v 前缀，git 与 gh 都用它。
    tag = "v" + version
    assert_clean(repo, allowed={"VERSION"} if read_version(repo) != version else set())
    command(["gh", "auth", "status"])
    run_checks(repo)

    previous_tag = previous_release_tag(public_repo, tag)
    notes = release_notes(tag, previous_tag, repo, args.notes_file)
    print("源码仓库: " + str(repo))
    print("目标 Release: " + public_repo)
    print("上一个 Release: " + (previous_tag or "无（首次发布）"))
    print("本次版本: " + version + "（标签 " + tag + "）")
    print("当前 VERSION: " + (read_version(repo) or "(空)"))
    print("Release 文案:")
    print(notes, end="")
    print("模式: publish" if args.publish else "模式: dry-run")

    if not args.publish:
        print("dry-run 完成；确认发布时加 --publish，将写入 VERSION、打标签并创建 GitHub Release。")
        return 0

    existing_release = release_exists(public_repo, tag)
    has_local = local_tag_exists(repo, tag)
    has_remote = remote_tag_exists(repo, tag)
    if (has_local or has_remote or existing_release) and not args.replace:
        raise ReleaseError("版本 " + tag + " 已存在标签或 Release；如需覆盖请明确使用 --replace")

    if read_version(repo) != version:
        write_version(repo, version)
        commit_version(repo, version)
    push_annotated_tag(repo, tag, "release(skill): 发布 easy-qfnu " + tag)
    confirm_tag_at_head(repo, tag)
    publish_github_release(public_repo, tag, notes, existing_release)
    print("发布完成: https://github.com/" + public_repo + "/releases/tag/" + tag)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReleaseError as exc:
        print("错误: " + str(exc), file=sys.stderr)
        raise SystemExit(1)
