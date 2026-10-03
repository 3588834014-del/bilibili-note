#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把本 skill 安装到 dsh 的用户 skill 目录。

    python scripts/install.py                 # 装到 ~/.agents/skills/bilibili-note
    python scripts/install.py --dry-run       # 只列出会复制什么
    python scripts/install.py --dest <dir>    # 换目标位置

只复制 skill 运行需要的文件，跳过 tests/、__pycache__、.git 等。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

SKILL_NAME = "bilibili-note"
# 运行 skill 只需要 SKILL.md、docs/、scripts/。tests/ 是开发用的，不装。
# bili_note_cli/ 是 CLI 入口点，dsh skill 用不到（skill 直接调 scripts/bili_note.py）。
INCLUDE = ["SKILL.md", "docs", "scripts"]
SKIP_NAMES = {"__pycache__", ".git", ".github", "tests", ".pytest_cache",
              ".ruff_cache", ".mypy_cache", "build", "dist", "node_modules",
              ".venv", "venv"}
# 构建产物（pip install 会生成），不属于要装的源码
SKIP_SUFFIXES = (".egg-info", ".pyc", ".pyo")


def find_source() -> Path:
    """本脚本所在仓库的根目录。"""
    return Path(__file__).resolve().parent.parent


def default_dest() -> Path:
    return Path.home() / ".agents" / "skills" / SKILL_NAME


def iter_files(root: Path):
    for name in INCLUDE:
        target = root / name
        if not target.exists():
            continue
        if target.is_file():
            yield target
            continue
        for path in sorted(target.rglob("*")):
            if path.is_dir():
                continue
            if any(part in SKIP_NAMES for part in path.parts):
                continue
            # 构建产物：pip install / egg-info / 编译缓存，都不是 skill 运行需要的
            if any(part.endswith(SKIP_SUFFIXES) for part in path.parts):
                continue
            if path.suffix in (".pyc", ".pyo"):
                continue
            yield path


def main() -> int:
    ap = argparse.ArgumentParser(description=f"安装 {SKILL_NAME} skill")
    ap.add_argument("--dest", type=Path, default=None,
                    help=f"目标目录（默认 {default_dest()}）")
    ap.add_argument("--dry-run", action="store_true", help="只列出会复制什么，不写入")
    args = ap.parse_args()

    src = find_source()
    dest = args.dest or default_dest()

    if not (src / "SKILL.md").is_file():
        print(f"✗ 在 {src} 下找不到 SKILL.md，这个脚本要在仓库内运行", file=sys.stderr)
        return 1

    files = list(iter_files(src))
    if not files:
        print("✗ 没有可安装的文件", file=sys.stderr)
        return 1

    print(f"源目录：{src}")
    print(f"目标目录：{dest}")
    print(f"待复制 {len(files)} 个文件：")
    for f in files:
        print(f"  {f.relative_to(src)}")

    if args.dry_run:
        print("\n（--dry-run，未写入任何文件）")
        return 0

    if dest.exists():
        print(f"\n目标已存在，先删除旧版本：{dest}")
        shutil.rmtree(dest)

    for f in files:
        rel = f.relative_to(src)
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, out)

    print(f"\n✓ 已安装到 {dest}")
    print("  重启或刷新 dsh 后即可用「B站笔记」之类的触发词唤起。")
    print("\n下一步：配置 B站 cookie（AI 字幕需要登录态）")
    print("  见 README 的「配置 cookie」一节。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
