"""从 skill 根目录的 VERSION 文件读取当前版本。"""

from pathlib import Path


def load_version():
    """读取 VERSION 文本。文件缺失或为空时返回 dev，避免启动失败。"""
    skill_root = Path(__file__).resolve().parents[2]
    path = skill_root / "VERSION"
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return "dev"
    if text == "":
        return "dev"
    return text


VERSION = load_version()
