#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bili-note 的命令行入口。

这个模块**只做一件事**：把命令行参数转交给确定性环节的实现（scripts/bili_note.py），
并在其上补一个 `note` 子命令 —— 一次跑完「抓字幕 + 写笔记」。

为什么不在这里写业务逻辑：同一套逻辑要同时被 CLI 和 dsh skill 使用，
放在一处才不会出现两个版本行为不一致。scripts/bili_note.py 是唯一的实现。

    bili-note fetch <URL> --out ./work
    bili-note note  <URL> --note-dir <vault>/Sources/Videos [--content-file c.json]
    bili-note write --work ./work --note-dir <vault>/Sources/Videos
    bili-note verify --note <笔记路径>
"""
from __future__ import annotations

import argparse
import sys


def _load_core():
    """定位并导入唯一实现 scripts/bili_note.py。

    两种来源都要支持：
      1. 从 PyPI/源码安装：顶层模块 `bili_note`（pyproject 里映射自 scripts/bili_note.py）
      2. 直接在 clone 出来的仓库里跑：同目录下没有包，但 scripts/ 就在隔壁
    """
    try:
        import bili_note as core  # type: ignore
        return core
    except ImportError:
        pass

    from pathlib import Path
    here = Path(__file__).resolve().parent
    for candidate in (here.parent / "scripts", here.parents[1] / "scripts"):
        if (candidate / "bili_note.py").is_file():
            sys.path.insert(0, str(candidate))
            import bili_note as core  # type: ignore
            return core

    raise SystemExit(
        "找不到 bili_note.py（确定性环节实现）。\n"
        "若你是 clone 仓库直接运行，请在仓库根目录执行；\n"
        "若已 pip 安装，请重新安装以修复包内容。"
    )


def _version(core) -> str:
    """版本号只认一处：安装了就读包元数据，否则用核心模块里的常量。

    避免 pyproject.toml 与 __version__ 各写一份后对不上。
    """
    try:
        from importlib.metadata import version as _v
        return _v("bilibili-note")
    except Exception:
        return getattr(core, "__version__", "0.0.0")


def _add_fetch_args(p) -> None:
    p.add_argument("url", help="视频完整 URL（推荐）或 BV 号；合集传裸 BV 号取全部，传 ?p=N 取单个分 P")
    p.add_argument("--out", help="工作目录（放 manifest / 字幕 / cookie 副本），默认 ./bili-note-work")


def _add_write_args(p, *, include_work: bool) -> None:
    if include_work:
        p.add_argument("--work", required=True, help="fetch 或 note 产生的工作目录")
    p.add_argument("--note-dir", help="笔记输出目录；缺省时读 BILI_VAULT/BILI_TARGET_DIR")
    p.add_argument("--content-file", "--summary-file", dest="summary_file",
                   help="判断性内容的 JSON（不给则写草稿）")
    p.add_argument("--processed", help=".processed.json 路径，用于去重记录")
    p.add_argument("--captured", help="覆盖捕获日期 YYYY-MM-DD")


DEFAULT_WORK = "./bili-note-work"


def cmd_note(args, core) -> int:
    """抓取 + 落盘，一步到位。"""
    work = args.out or DEFAULT_WORK
    rc = core.cmd_fetch(argparse.Namespace(url=args.url, out=work))
    if rc != 0:
        return rc
    return core.cmd_write(argparse.Namespace(
        work=work,
        note_dir=args.note_dir,
        summary_file=args.summary_file,
        processed=args.processed,
        captured=args.captured,
    ))


def build_parser(core) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="bili-note",
        description="把 B站视频的字幕取下来，生成结构化的 Obsidian 笔记（只拉字幕，不下载视频）。",
        epilog="示例：bili-note note 'https://www.bilibili.com/video/BVxxxx' "
               "--note-dir ~/MyVault/Sources/Videos",
    )
    ap.add_argument("--version", action="version", version=f"bili-note {_version(core)}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_note = sub.add_parser("note", help="抓取 + 写笔记（一步到位）")
    _add_fetch_args(p_note)
    _add_write_args(p_note, include_work=False)

    p_fetch = sub.add_parser("fetch", help="只抓元数据 + 字幕，产出 manifest.json")
    _add_fetch_args(p_fetch)
    p_fetch.add_argument("--out-required", action="store_true", help=argparse.SUPPRESS)

    p_write = sub.add_parser("write", help="用已有工作目录组装并写入笔记")
    _add_write_args(p_write, include_work=True)

    p_verify = sub.add_parser("verify", help="笔记结构自检")
    p_verify.add_argument("--note", required=True, help="笔记路径")
    p_verify.add_argument("--min-bullets", type=int, default=5)

    p_self = sub.add_parser("selftest", help="环境自检：cookie / yt-dlp / 输出目录（不联网）")
    p_self.add_argument("--note-dir", help="顺便检查这个输出目录")

    return ap


def _force_utf8_stdio() -> None:
    """把 stdout/stderr 切到 UTF-8。

    **必须放在 main() 里，不能只放在 `if __name__ == "__main__"`。**
    控制台脚本（pyproject 的 [project.scripts]）会调用 `main()`，但不会执行
    模块底部的 `__main__` 块；而 Windows 的默认控制台编码是 cp1252/cp936，
    打印中文帮助或错误信息时直接抛 UnicodeEncodeError：

        UnicodeEncodeError: 'charmap' codec can't encode character '\\u628a'

    这个 bug 只在"真正 pip 安装后在 Windows 上跑"这一条路径上出现，
    所以是 CI 的 package job 抓到的，仓库内直接跑和 Linux 都正常。
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                # 流被重定向/已关闭时 reconfigure 可能失败，不该因此让 CLI 挂掉
                pass


def main(argv=None) -> int:
    _force_utf8_stdio()
    core = _load_core()
    ap = build_parser(core)
    args = ap.parse_args(argv)

    try:
        if args.cmd == "note":
            return cmd_note(args, core)
        if args.cmd == "fetch":
            return core.cmd_fetch(argparse.Namespace(url=args.url, out=args.out or DEFAULT_WORK))
        if args.cmd == "write":
            return core.cmd_write(args)
        if args.cmd == "verify":
            return core.cmd_verify(args)
        if args.cmd == "selftest":
            return core.cmd_selftest(args)
    except core.BiliNoteError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已中断", file=sys.stderr)
        return 130
    ap.error(f"未知子命令：{args.cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
