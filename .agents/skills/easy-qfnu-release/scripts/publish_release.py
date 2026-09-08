#!/usr/bin/env python3
"""Publish an easy-qfnu date-tagged GitHub Release from the Python skill repo."""

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

VERSION_RE = re.compile(r"^v[0-9]{4}[.][0-9]{2}[.][0-9]{2}[.][0-9]{2}$")
COMMIT_RE = re.compile(
    r"^(?P<type>[a-z]+)(?:[(](?P<scope>[^)]*)[)])?(?:!)?:[ \t]*(?P<subject>.+)$",
    re.IGNORECASE,
)
DATE_TAG_RE = re.compile(r"^v[0-9]{4}[.][0-9]{2}[.][0-9]{2}[.](?:[0-9]{2}|[0-9]{4})$")

FEATURE_TYPES = {"feat", "feature"}
FIX_TYPES = {"fix", "bugfix", "perf"}


class ReleaseError(RuntimeError):
    """A release precondition or publication step failed."""


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


def release_exists(public_repo, version):
    result = subprocess.run(
        ["gh", "release", "view", version, "--repo", public_repo],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        return True
    error = (result.stderr or result.stdout).lower()
    if "release not found" in error or "not found" in error:
        return False
    raise ReleaseError("无法检查公共 Release " + version + ": " + (result.stderr or result.stdout).strip())


def previous_release_tag(public_repo, current_version):
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
        if not tag or not DATE_TAG_RE.fullmatch(tag):
            continue
        if tag == current_version:
            continue
        if release.get("isDraft") or release.get("isPrerelease"):
            continue
        if not release.get("publishedAt"):
            continue
        published.append(release)
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


def release_notes(version, previous_tag, repo, notes_file):
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
        change_range = "`" + previous_tag + "` → `" + version + "`"
    else:
        change_range = "首次日期版本"
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
        "| Release | `" + version + "` |",
        "| CLI | `" + version + "` |",
        "| skill | `" + version + "` |",
        "",
        "本地 `VERSION` 文件与 Release 标签相同。使用前请先更新到最新版本。",
    ]
    return "\n".join(lines) + "\n"


def remote_tag_exists(repo, version):
    result = subprocess.run(
        ["git", "ls-remote", "--exit-code", "--tags", "origin", "refs/tags/" + version],
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


def local_tag_exists(repo, version):
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "refs/tags/" + version],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def tagged_commit(repo, version):
    return command_text(["git", "rev-parse", version + "^{commit}"], cwd=repo)


def push_annotated_tag(repo, version, message):
    local_tag = local_tag_exists(repo, version)
    remote_tag = remote_tag_exists(repo, version)
    tag_args = ["git", "tag", "-a", version, "-m", message]
    if local_tag:
        tag_args.insert(2, "-f")
    command(tag_args, cwd=repo)
    push_args = ["git", "push", "origin", version]
    if remote_tag:
        push_args.insert(2, "--force")
    command(push_args, cwd=repo)


def confirm_tag_at_head(repo, version):
    head = command_text(["git", "rev-parse", "HEAD"], cwd=repo)
    if tagged_commit(repo, version) != head:
        raise ReleaseError(str(repo) + " 的标签 " + version + " 没有指到当前 HEAD")


def porcelain_paths(repo):
    text = command_text(["git", "status", "--porcelain"], cwd=repo)
    paths = []
    for line in text.splitlines():
        if not line.strip():
            continue
        body = line[3:]
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
    (repo / "VERSION").write_text(version + "\n", encoding="utf-8")


def commit_version(repo, version):
    command(["git", "add", "VERSION"], cwd=repo)
    command(["git", "commit", "-m", "chore(release): 🚀 发布 easy-qfnu " + version], cwd=repo)
    command(["git", "push", "origin", "HEAD"], cwd=repo)


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


def publish_github_release(public_repo, version, notes, existing_release):
    if existing_release:
        command(["gh", "release", "delete", version, "--repo", public_repo, "--yes"])
    command(
        [
            "gh",
            "release",
            "create",
            version,
            "--repo",
            public_repo,
            "--title",
            version,
            "--notes",
            notes,
        ]
    )


def parse_args():
    today = dt.datetime.now().astimezone().strftime("v%Y.%m.%d.%H")
    parser = argparse.ArgumentParser(description="Publish easy-qfnu date releases")
    parser.add_argument("--repo", type=Path, default=None, help="local skill repository")
    parser.add_argument("--public-repo", default=None, help="public release repository")
    parser.add_argument("--version", default=today, help="date tag (default: " + today + ")")
    parser.add_argument("--notes-file", type=Path, default=None, help="可选的 Release 文案文件")
    parser.add_argument("--publish", action="store_true", help="write VERSION, create/push tag and upload release")
    parser.add_argument("--replace", action="store_true", help="replace an existing same-hour tag/release")
    return parser.parse_args()


def main():
    args = parse_args()
    if not VERSION_RE.fullmatch(args.version):
        raise ReleaseError("版本必须使用 vYYYY.MM.DD.HH 格式，例如 v2026.08.30.14")
    default_repo = default_skill_repo()
    repo = (args.repo or Path(os.environ.get("EASY_QFNU_SKILL_REPO", default_repo))).expanduser().resolve()
    public_repo = args.public_repo or os.environ.get("EASY_QFNU_PUBLIC_REPO", "w1ndys/easy-qfnu-skill")
    if args.replace and not args.publish:
        raise ReleaseError("--replace 只能与 --publish 一起使用")
    if args.notes_file and not args.notes_file.is_file():
        raise ReleaseError("找不到 Release 文案文件: " + str(args.notes_file))

    validate_repo(repo)
    assert_clean(repo, allowed={"VERSION"} if read_version(repo) != args.version else set())
    command(["gh", "auth", "status"])
    run_checks(repo)

    previous_tag = previous_release_tag(public_repo, args.version)
    notes = release_notes(args.version, previous_tag, repo, args.notes_file)
    print("源码仓库: " + str(repo))
    print("目标 Release: " + public_repo)
    print("上一个 Release: " + (previous_tag or "无（首次日期版本）"))
    print("VERSION/Release 统一版本: " + args.version)
    print("当前 VERSION: " + (read_version(repo) or "(空)"))
    print("Release 文案:")
    print(notes, end="")
    print("模式: publish" if args.publish else "模式: dry-run")

    if not args.publish:
        print("dry-run 完成；确认发布时加 --publish，将写入 VERSION、打标签并创建 GitHub Release。")
        return 0

    existing_release = release_exists(public_repo, args.version)
    has_local = local_tag_exists(repo, args.version)
    has_remote = remote_tag_exists(repo, args.version)
    if (has_local or has_remote or existing_release) and not args.replace:
        raise ReleaseError("版本 " + args.version + " 已存在标签或 Release；如需覆盖请明确使用 --replace")

    if read_version(repo) != args.version:
        write_version(repo, args.version)
        commit_version(repo, args.version)
    push_annotated_tag(repo, args.version, "release(skill): 🚀 发布 easy-qfnu " + args.version)
    confirm_tag_at_head(repo, args.version)
    publish_github_release(public_repo, args.version, notes, existing_release)
    print("发布完成: https://github.com/" + public_repo + "/releases/tag/" + args.version)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReleaseError as exc:
        print("错误: " + str(exc), file=sys.stderr)
        raise SystemExit(1)
