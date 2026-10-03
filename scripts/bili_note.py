#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bilibili-note 的确定性环节：字幕获取、解析、笔记落盘、结构自检。

本脚本只做「机械动作」，不做任何内容判断：
  判断性内容（一句话摘要 / 核心要点 / 总结 / 标签）由 agent 按 SKILL.md 的
  输入输出规约生成，再通过 `write --summary-file` 填进来。

子命令
    fetch   URL [--out DIR]                 抓元数据 + 字幕，产出 manifest.json
    write   --work DIR --note-dir DIR [...] 组装并写入 Obsidian 笔记
    verify  --note PATH [--min-bullets N]   结构自检

依赖：yt-dlp（唯一下载通道），Python 3.8+ 标准库。无第三方包。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

__version__ = "1.0.0"

# ---------------------------------------------------------------- 常量

ILLEGAL_CHARS = r'[<>:"/\\|?*#\x00-\x1f\[\]]'

# 字幕语言优先级：先 AI 中文，再人工中文，最后任意中文变体
SUB_LANG_PRIORITY = ["ai-zh", "ai-zh-Hans", "ai-zh-CN", "zh-CN", "zh-Hans", "zh"]


class BiliNoteError(Exception):
    """可预期的流程错误（无字幕、缺 cookie、yt-dlp 缺失等）。"""


# ---------------------------------------------------------------- 工具


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def find_ytdlp() -> str:
    """定位 yt-dlp；优先 PATH，其次 python -m yt_dlp。"""
    exe = shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")
    if exe:
        return exe
    try:
        probe = subprocess.run(
            [sys.executable, "-m", "yt_dlp", "--version"],
            capture_output=True, text=True, timeout=60,
        )
        if probe.returncode == 0:
            return f"{sys.executable} -m yt_dlp"
    except (OSError, subprocess.SubprocessError):
        pass
    raise BiliNoteError("未找到 yt-dlp，请先安装：pip install -U yt-dlp")


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    """执行子进程，强制 UTF-8 解码（Windows 控制台默认 GBK 会炸）。"""
    return subprocess.run(
        cmd, capture_output=True,
        encoding="utf-8", errors="replace", timeout=600,
    )


def bvid_of(url_or_bvid: str) -> str:
    m = re.search(r"(BV[0-9A-Za-z]+)", url_or_bvid)
    if not m:
        raise BiliNoteError(f"无法从输入中识别 BV 号：{url_or_bvid}")
    return m.group(1)


def norm_url(url_or_bvid: str) -> str:
    """一律规范成不带追踪参数的完整 URL。

    坑位一：把裸 BV 号直接给下游时，frontmatter 的 source 会只写裸 BV，
    导致 Obsidian 里不可点击、后续管线按 URL 正则也匹配不到。

    坑位二：从浏览器复制来的链接常带一堆追踪参数
    （`?spm_id_from=...&vd_source=...`）。这些必须剥掉，否则：
      - frontmatter 的 source 与全库 210 篇的规范形状不一致；
      - 要点跳链会被拼成
        `.../video/BVxxx/?spm_id_from=...&vd_source=.../?t=23s`
        出现两个 `?`，B站解析不了。
    """
    raw = url_or_bvid.strip()
    if raw.startswith("http"):
        m = re.search(r"(?:bilibili\.com/video/|b23\.tv/)(BV[0-9A-Za-z]+)", raw)
        if m:
            return f"https://www.bilibili.com/video/{m.group(1)}"
        # 不是可识别的视频页链接：原样返回，交给 yt-dlp 判断
        return raw
    return f"https://www.bilibili.com/video/{bvid_of(raw)}"


def fmt_ts(seconds) -> str:
    """秒 -> M:SS 或 H:MM:SS。

    用于 frontmatter 的 duration 字段（语料一律是 `"5:24"` / `"2:10:35"`）。
    注意：要点行的时间戳**不用**这个函数，见 fmt_bullet_ts。
    """
    total = int(float(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_bullet_ts(seconds) -> str:
    """秒 -> 要点行标签 MM:SS（超 1 小时用总分钟数，如 93:31）。

    刻意不做小时进位：这是与下游归档管线对齐的结果，`03:00:00` 之后的
    时间戳会写成 `[180:00]` 而不是 `[3:00:00]`。详见 docs/FORMAT.md。
    """
    total = int(float(seconds))
    return f"{total // 60:02d}:{total % 60:02d}"


def sanitize_filename(title: str) -> str:
    """替换 Windows 非法字符。

    除 Windows 保留字符外，额外去掉两类 Obsidian 特有的坑：
      - 方括号 `[]`：wikilink 的定界符，留在文件名里会让 `[[...]]` 解析失败。
      - `#`：被视作标题锚点分隔符，会把链接从 `#` 处截断。
    """
    cleaned = re.sub(ILLEGAL_CHARS, " ", title).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or "未命名视频"


# ---------------------------------------------------------------- 路径


def resolve_note_dir(explicit: str | None) -> Path:
    """按优先级定位笔记输出目录。

    优先级：显式传参 > BILI_VAULT (+ BILI_TARGET_DIR) > 报错要求明确指定。
    刻意不内置任何默认库路径：不同机器的 vault 位置不同，硬编码一个
    "看起来对"的路径只会在别处静默创建出空目录，笔记就此失踪。
    """
    if explicit:
        return Path(explicit).expanduser()

    vault = os.environ.get("BILI_VAULT")
    if vault:
        target = os.environ.get("BILI_TARGET_DIR") or "Sources/Videos"
        return Path(vault).expanduser() / target

    raise BiliNoteError(
        "未指定笔记输出目录。请传 --note-dir，或设置环境变量 BILI_VAULT 指向 Obsidian 库根"
        "（可选 BILI_TARGET_DIR，默认 Sources/Videos）。"
    )


# ---------------------------------------------------------------- cookie


def resolve_cookies() -> Path | None:
    """按优先级定位 B 站 cookie 文件。

    只查两处通用位置，不猜测浏览器 profile 目录那种脆弱路径。
    """
    env = os.environ.get("BILI_COOKIES")
    if env and Path(env).is_file():
        return Path(env)
    local = Path.cwd() / "cookies.txt"
    if local.is_file():
        return local
    return None


def normalize_cookies(src: Path, dst: Path) -> int:
    """把浏览器导出的 Netscape cookie 归一成 yt-dlp 能读的规范格式。

    为什么必须做这一步：
      「Get cookies.txt LOCALLY」导出的行是 `.bilibili.com <TAB> FALSE ...`，
      即 domain 带前导点、第 2 列却是 host-only 标志。Python 的
      http.cookiejar 有一条断言 `domain_specified == initial_dot`，
      于是 yt-dlp 直接报 `invalid Netscape format cookies file` 而拒绝加载。
      归一方式：去掉 domain 前导点并令 domain_specified=FALSE（host-only），
      host-only cookie 仍然会发送给 api.bilibili.com 等子域，登录态不丢。

    另：导出文件通常混有多个站点的 cookie，只保留 bilibili 域，
    顺带去重（同 domain/path/name 只留一条，后出现的覆盖先出现的）。
    """
    if not src.is_file():
        raise BiliNoteError(f"cookie 文件不存在：{src}")

    rows: dict[str, str] = {}
    with src.open("r", encoding="utf-8-sig", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\r\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7 or "bilibili" not in parts[0]:
                continue
            domain = parts[0].lstrip(".")
            path, secure, expires, name, value = parts[2], parts[3], parts[4], parts[5], parts[6]
            rows[f"{domain}\t{path}\t{name}"] = (
                f"{domain}\tFALSE\t{path}\t{secure}\t{expires}\t{name}\t{value}"
            )

    if not rows:
        raise BiliNoteError(
            f"{src} 里没有 bilibili 域的 cookie——请重新用扩展导出并确认已登录 bilibili.com"
        )

    dst.parent.mkdir(parents=True, exist_ok=True)
    body = "# Netscape HTTP Cookie File\n" + "\n".join(rows.values()) + "\n"
    write_text_lf(dst, body)
    return len(rows)


def write_text_lf(path: Path, text: str) -> None:
    """以 UTF-8 + LF 写文本，兼容 Python 3.8。

    `Path.write_text(..., newline=)` 是 Python 3.10 才加的；本 skill 声明支持
    3.8+，所以这里退回 `open()` —— `open(newline="\\n")` 从 3.8 起就可用。
    统一走这个函数可以保证任何平台上产物都是 LF、无 BOM。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def cookie_has_login(path: Path) -> bool:
    """粗查是否含登录态（SESSDATA）；没有它拿不到 AI 字幕。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "SESSDATA" in text


# ---------------------------------------------------------------- yt-dlp


def ytdlp_meta(ytdlp: str, url: str, cookies: Path | None = None) -> dict:
    """--dump-json：拿元数据。此模式不下载、也不写字幕。

    带上 cookie：B站对未登录请求的元数据响应不稳定，且登录态下才能拿到
    完整的视频信息。
    """
    cmd = [*ytdlp.split()]
    if cookies:
        cmd += ["--cookies", str(cookies)]
    cmd += ["--dump-json", "--skip-download", "--no-warnings", url]
    proc = run(cmd)
    if proc.returncode != 0:
        raise BiliNoteError(f"yt-dlp 元数据抓取失败：\n{(proc.stderr or '').strip()[-800:]}")
    line = next((ln for ln in proc.stdout.splitlines() if ln.strip().startswith("{")), None)
    if not line:
        raise BiliNoteError("yt-dlp 未返回元数据 JSON")
    return json.loads(line)


def ytdlp_list_subs(ytdlp: str, url: str, cookies: Path) -> list[str]:
    """列出可用字幕语言（解析 `--list-subs` 的表格）。

    为什么不用元数据 JSON 的 `subtitles` / `automatic_captions`：实测
    `--dump-json` 对 B站返回的两个键都是空的，拿不到 AI 字幕清单。
    `--list-subs` 才能列出 `ai-zh` 等语言。

    解析要宽容：只认 "Language <TAB> Formats" 表头之后的 `语言  格式` 行，
    避免把 `[BiliBili] ...` 之类的日志行当成语言。
    """
    cmd = [
        *ytdlp.split(), "--cookies", str(cookies),
        "--list-subs", "--skip-download", "--no-warnings", url,
    ]
    proc = run(cmd)
    langs: list[str] = []
    in_table = False
    for line in proc.stdout.splitlines():
        s = line.strip()
        if s.startswith("Language"):
            in_table = True
            continue
        if not in_table or not s:
            continue
        m = re.match(r"^([A-Za-z][\w-]*)\s+(\S+)$", s)
        if m:
            langs.append(m.group(1))
    return list(dict.fromkeys(langs))


def pick_lang(available: list[str]) -> str | None:
    """按优先级挑一个中文语言码。"""
    for candidate in SUB_LANG_PRIORITY:
        if candidate in available:
            return candidate
    for candidate in available:
        if candidate.startswith("ai-zh") or candidate.startswith("zh"):
            return candidate
    return None


def ytdlp_subs(ytdlp: str, url: str, cookies: Path, lang: str, work: Path) -> Path:
    """只拉字幕，绝不拉视频。

    关键约束：--skip-download 只写字幕；-o 用纯 BV 号作 basename，
    避免中文标题在部分终端/编码环境下出错。
    """
    cmd = [
        *ytdlp.split(), "--cookies", str(cookies),
        "--skip-download",
        "--write-subs", "--write-auto-subs",
        "--sub-langs", lang,
        "--sub-format", "srt/best",
        "--paths", str(work),
        "-o", "%(id)s",
        "--no-warnings",
        url,
    ]
    proc = run(cmd)
    if proc.returncode != 0:
        raise BiliNoteError(f"yt-dlp 字幕下载失败：\n{(proc.stderr or '').strip()[-800:]}")

    bvid = bvid_of(url)
    # 优先精确匹配 `<bvid>.<lang>.srt`；不行再退回 `<bvid>*.srt`（有些语言码
    # 会被 yt-dlp 规范化，例如传 zh-CN 实际写出 zh-CN.srt 之外的变体）。
    exact = work / f"{bvid}.{lang}.srt"
    if exact.is_file():
        return exact
    hits = sorted(work.glob(f"{bvid}*.srt"))
    if not hits:
        raise BiliNoteError(
            f"yt-dlp 未写出字幕文件（语言 {lang}）。"
            f"通常是该视频没有对应字幕，或 cookie 登录态已失效。"
        )
    return hits[0]


# ---------------------------------------------------------------- SRT


def parse_srt(text: str) -> list[dict]:
    """SRT -> [{"time": 秒, "text": ...}]，并做时间戳清理。

    清理内容：序号行、时间码行、HTML/ASS 标签、BOM、零宽字符、重复行。
    """
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    segs: list[dict] = []
    for block in re.split(r"\n{2,}", text):
        lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        idx = 0
        if re.fullmatch(r"\d+", lines[0]):
            idx = 1
        if idx >= len(lines):
            continue
        m = re.match(
            r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})",
            lines[idx],
        )
        if not m:
            continue
        start = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4)) / 1000
        body = " ".join(lines[idx + 1:]).strip()
        # 只去真正的标签形态。不要用 `<[^>]+>`：字幕正文里出现数学比较
        # （如「a < b > c」）时那会把正文一并吃掉。
        body = re.sub(
            r"</?(?:i|b|u|s|em|strong|font|ruby|rt|c|v|p|span|br)\b[^>]*/?>",
            "", body, flags=re.IGNORECASE,
        )
        body = re.sub(r"\{\\[^}]*\}", "", body)          # ASS 覆盖码
        body = body.replace("\u200b", "").replace("\ufeff", "")
        body = re.sub(r"\s+", " ", body).strip()
        if not body:
            continue
        # B 站 AI 字幕常有整句重复的滚动字幕，去掉与前一条完全相同的文本
        if segs and body == segs[-1]["text"]:
            continue
        segs.append({"time": start, "text": body})
    return segs


def segments_to_text(segs: list[dict]) -> str:
    return " ".join(s["text"] for s in segs)


# ---------------------------------------------------------------- fetch


def cmd_fetch(args) -> int:
    ytdlp = find_ytdlp()
    url = norm_url(args.url)
    bvid = bvid_of(url)
    work = Path(args.out).resolve()
    work.mkdir(parents=True, exist_ok=True)

    src = resolve_cookies()
    if not src:
        raise BiliNoteError(
            "找不到 B 站 cookie 文件。请用「Get cookies.txt LOCALLY」导出后放到当前目录的 "
            "cookies.txt，或设置环境变量 BILI_COOKIES 指向它。"
        )
    n = normalize_cookies(src, work / "cookies.netscape.txt")
    cookies = work / "cookies.netscape.txt"
    if not cookie_has_login(cookies):
        log("! 警告：cookie 里没有 SESSDATA，AI 字幕大概率拿不到（仅 UP 主上传的字幕可用）")
    log(f"✓ cookie 归一化：{src} -> {cookies}（{n} 条 bilibili 域 cookie）")

    meta = ytdlp_meta(ytdlp, url, cookies)
    title = (meta.get("title") or "").strip()
    uploader = (meta.get("uploader") or meta.get("channel") or "").strip()
    duration = float(meta.get("duration") or 0)
    log(f"✓ 元数据：{title}（{uploader or '未知UP'}）｜时长 {fmt_ts(duration)}")

    # 字幕语言协商。
    # 先试 `--list-subs` 拿真实清单；拿不到（B站对密集请求会临时限流，
    # 表现为清单为空）就按优先顺序逐个真下载探测，而不是直接判定"无字幕"
    # 放弃这个视频。
    available = ytdlp_list_subs(ytdlp, url, cookies)
    log(f"  可用字幕语言：{', '.join(available) if available else '(清单为空)'}")

    candidates: list[str] = []
    if available:
        first = pick_lang(available)
        if first:
            candidates.append(first)
    for lang in SUB_LANG_PRIORITY:
        if lang not in candidates:
            candidates.append(lang)

    if not available:
        log("  ! 字幕清单为空（可能是临时限流），改为逐个语言探测")

    srt_path = None
    chosen = None
    errors: list[str] = []
    for lang in candidates:
        try:
            srt_path = ytdlp_subs(ytdlp, url, cookies, lang, work)
            chosen = lang
            break
        except BiliNoteError as exc:
            errors.append(f"{lang}: {exc}")
    if srt_path is None or chosen is None:
        detail = "; ".join(errors[:3]) if errors else "无候选语言"
        raise BiliNoteError(
            f"该视频没有可用中文字幕，或 cookie 登录态已失效。"
            f"已尝试 {len(candidates)} 种语言（{detail}）。按约定跳过，不生成空笔记。"
        )
    log(f"✓ 字幕语言命中：{chosen}")
    segs = parse_srt(srt_path.read_text(encoding="utf-8", errors="replace"))
    if not segs:
        raise BiliNoteError(f"字幕解析后为空：{srt_path}")
    log(f"✓ 字幕：{chosen}，{len(segs)} 段，{len(segments_to_text(segs))} 字")

    # 确认没有误拉媒体文件
    junk = [p.name for p in work.iterdir()
            if p.suffix.lower() in (".mp4", ".flv", ".m4a", ".webm", ".mp3", ".part", ".f30280", ".f100026")]
    if junk:
        log(f"! 警告：工作目录出现媒体文件（本流程不应下载视频）：{junk}")

    manifest = {
        "bvid": bvid,
        "url": url,
        "title": title,
        "uploader": uploader,
        "duration": duration,
        "duration_hms": fmt_ts(duration),
        "subtitle_lang": chosen,
        "subtitle_available": available,
        "srt": str(srt_path),
        "segment_count": len(segs),
        "subtitle_text": segments_to_text(segs),
        "segments": segs,
    }
    write_text_lf(work / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    log(f"✓ manifest：{work / 'manifest.json'}")
    return 0


# ---------------------------------------------------------------- write


def build_frontmatter(m: dict, tags: list[str], status: str, captured: str) -> str:
    """frontmatter 逐字段写死。

    tags 默认留空数组：标签由后续归档管线/agent 填，本环节不猜。
    """
    return (
        "---\n"
        f"title: {m['title']}\n"
        f"source: {m['url']}\n"
        f"author: {m['uploader'] or '未知'}\n"
        f"captured: {captured}\n"
        f'duration: "{m["duration_hms"]}"\n'
        "type: video-note\n"
        f"status: {status}\n"
        f"needs_summary: {'true' if status == 'draft' else 'false'}\n"
        f"tags: {json.dumps(tags, ensure_ascii=False)}\n"
        "---\n"
    )


PLACEHOLDER_SUMMARY = "<!-- 待总结：用 1 句话概括视频讲了什么、结论是什么，60-120 字 -->"
PLACEHOLDER_BULLETS = "<!-- 待总结：从下方字幕提炼 5-10 条要点，格式 |- [MM:SS](URL/?t=Ns) **小标题**：说明 -->"
PLACEHOLDER_BODY = "<!-- 待总结：200-400 字，讲清楚视频的论证链条与结论 -->"
PLACEHOLDER_TAGS = "<!-- 待总结：从 _tags.md 白名单选 1-3 个 -->"


def build_note(m: dict, summary: dict | None, captured: str) -> str:
    """组装笔记。

    分两种成熟度：
      - 无 summary：草稿，判断性段落留占位符，status: draft
      - 有 summary：完成态，status: completed，并附段落时间戳表供归档用
    """
    done = bool(summary)
    status = "completed" if done else "draft"
    tags = (summary or {}).get("tags") or []

    one_liner = (summary or {}).get("summary") or PLACEHOLDER_SUMMARY
    body_text = (summary or {}).get("body") or PLACEHOLDER_BODY

    bullets = (summary or {}).get("bullets") or []
    if bullets:
        # 要点行格式必须与下游归档管线对齐：
        #   |- [MM:SS]({URL}/?t={秒}s) 说明
        # 两个硬约束，改动前先读 docs/FORMAT.md：
        #   1. 行首必须是 `|- ` 一个空格：下游校验器的正则是
        #      ^\|- \[[0-9]{2}:[0-9]{2}\]，只认这一种形状。
        #      写成标准 markdown 列表 `- [...]` 会被判「要点不足」。
        #   2. 标签用总分钟数（fmt_bullet_ts），不要小时进位。
        lines = []
        for b in bullets:
            t = float(b["time"])
            desc = f"**{b['title']}**：{b['text']}" if b.get("title") else b["text"]
            lines.append(f"|- [{fmt_bullet_ts(t)}]({m['url']}/?t={int(t)}s) {desc}")
        bullets_md = "\n".join(lines)
    else:
        bullets_md = PLACEHOLDER_BULLETS

    parts = [
        "> [!info] 视频信息",
        f"> **UP主**：{m['uploader'] or '未知'} ｜ **时长**：{m['duration_hms']} ｜ [原视频]({m['url']})",
        "",
        "## 一句话摘要",
        "",
        one_liner,
        "",
        "## 核心要点",
        "",
        bullets_md,
        "",
        "## 总结",
        "",
        body_text,
        "",
        "## 笔记标签",
        "",
        " ｜ ".join(tags) if tags else PLACEHOLDER_TAGS,
        "",
    ]

    cards = (summary or {}).get("cards") or []
    if cards:
        parts += ["## 相关卡片", ""] + [f"- [[{c}]]" for c in cards] + [""]

    # 段落时间戳表：把带时间戳的字幕段整理成表格，供归档/复核用
    segs = m.get("segments") or []
    if done and segs:
        parts += ["## 段落时间戳", "", "| 时间 | 内容 |", "|------|------|"]
        for s in segs:
            parts.append(f"| {fmt_ts(s['time'])} | {s['text']} |")
        parts.append("")

    parts += [
        "## 原始字幕",
        "",
        "> [!quote]- 完整字幕（点击展开）",
        "> " + m["subtitle_text"].replace("\n", "\n> "),
        "",
    ]

    return build_frontmatter(m, tags, status, captured) + "\n".join(parts).rstrip() + "\n"


def cmd_write(args) -> int:
    work = Path(args.work).resolve()
    mpath = work / "manifest.json"
    if not mpath.is_file():
        raise BiliNoteError(f"缺少 manifest：{mpath}（先跑 fetch）")
    m = json.loads(mpath.read_text(encoding="utf-8"))

    # 防御：老版本 fetch 写出的 manifest 里 url 可能带追踪参数
    # （`?spm_id_from=...`）。这里统一再过一次规范化，否则 source 会带脏参数，
    # 且要点跳链会被拼成含两个 `?` 的坏链接。
    m["url"] = norm_url(m.get("url") or m.get("bvid") or "")

    summary = None
    if args.summary_file:
        sp = Path(args.summary_file)
        if not sp.is_file():
            raise BiliNoteError(f"summary 文件不存在：{sp}")
        summary = json.loads(sp.read_text(encoding="utf-8"))

    note_dir = resolve_note_dir(args.note_dir)
    note_dir.mkdir(parents=True, exist_ok=True)

    captured = args.captured or datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    filename = f"B站 {sanitize_filename(m['title'])}.md"
    path = note_dir / filename

    # 同名不同视频：追加 BV 号，避免后者覆盖前者
    if path.exists():
        head = path.read_text(encoding="utf-8", errors="replace")[:1500]
        other = re.search(r"source: https://www\.bilibili\.com/video/(BV\w+)", head)
        if other and other.group(1) != m["bvid"]:
            path = note_dir / f"B站 {sanitize_filename(m['title'])} {m['bvid']}.md"

    write_text_lf(path, build_note(m, summary, captured))
    log(f"✓ 已写入笔记：{path}")
    if not summary:
        log("  → 这是草稿（status: draft）。摘要/要点/总结/标签待补全。")

    # 去重记录
    if args.processed:
        ppath = Path(args.processed)
        seen: list[str] = []
        if ppath.is_file():
            try:
                data = json.loads(ppath.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    seen = data
            except json.JSONDecodeError:
                log(f"! {ppath} 不是合法 JSON，将重建")
        if m["bvid"] not in seen:
            seen.append(m["bvid"])
            write_text_lf(ppath, json.dumps(seen, ensure_ascii=False, indent=2))
            log(f"✓ 去重记录已更新：{ppath}（{len(seen)} 条）")

    print(path)
    return 0


# ---------------------------------------------------------------- verify


def cmd_verify(args) -> int:
    path = Path(args.note).resolve()
    if not path.is_file():
        raise BiliNoteError(f"笔记不存在：{path}")
    text = path.read_text(encoding="utf-8", errors="replace")
    problems: list[str] = []

    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        problems.append("含 BOM")
    if "\ufeff" in text:
        problems.append("正文含 BOM 字符")

    # frontmatter
    fm = {}
    if not text.startswith("---\n"):
        problems.append("缺少 frontmatter")
    else:
        block = text.split("---\n", 2)[1]
        for ln in block.splitlines():
            if ":" in ln:
                k, v = ln.split(":", 1)
                fm[k.strip()] = v.strip()
    for key in ("title", "source", "author", "captured", "duration", "type", "status", "needs_summary", "tags"):
        if key not in fm:
            problems.append(f"frontmatter 缺字段 {key}")
    if fm.get("source") and not re.fullmatch(r"https://www\.bilibili\.com/video/BV\w+", fm["source"]):
        problems.append(f"source 不是规范完整 URL：{fm.get('source')}")
    if fm.get("tags") not in ("[]", None) and not fm.get("tags", "").startswith("["):
        problems.append(f"tags 不是数组：{fm.get('tags')}")

    draft = fm.get("status") == "draft"
    leftovers = [p for p in ("待总结", "待Claudian总结") if p in text]
    if draft and not leftovers:
        problems.append("status=draft 但找不到待总结占位符")
    if not draft and leftovers:
        problems.append("status 非 draft 但仍有占位符残留")

    # 要点行：对齐下游归档管线的形状 `|- [MM:SS](…) …`。
    # 同时容忍标准 markdown 列表 `- [MM:SS](…)`，以免手工写的笔记直接被判死；
    # 分钟数放开到 1-3 位，`[123:49]` 这种超 1 小时的总分钟写法才算合法。
    bullets = re.findall(r"^(?:\|-|-) \[(\d{1,3}:\d{2})\]\((\S+?)\)", text, re.M)
    if not draft:
        if len(bullets) < args.min_bullets:
            problems.append(f"要点数 {len(bullets)} < {args.min_bullets}")
        for ts, link in bullets:
            if "?t=" not in link:
                problems.append(f"要点跳链缺 ?t= 参数：{link}")

    if "## 原始字幕" not in text:
        problems.append("缺少「## 原始字幕」章节")
    elif "> [!quote]- 完整字幕（点击展开）" not in text:
        problems.append("原始字幕折叠块标题不符")
    if "> [!info] 视频信息" not in text:
        problems.append("缺少「视频信息」callout")
    for head in ("## 一句话摘要", "## 核心要点", "## 总结", "## 笔记标签"):
        if head not in text:
            problems.append(f"缺少章节 {head}")

    # 检查字幕正文是否真的抓到了内容。
    # 刻意不检查「笔记总字符数」：一个 legitimately 短的视频（或一段短字幕）
    # 会因为总长度小而误报，那不是错误。这里只关心字幕本身是否为空壳。
    subtitle_body = ""
    if "## 原始字幕" in text:
        subtitle_body = text.split("## 原始字幕", 1)[1]
        subtitle_body = "\n".join(ln[2:] for ln in subtitle_body.splitlines() if ln.startswith("> "))
    if len(subtitle_body.strip()) < 50:
        problems.append(f"字幕正文过短或为空（{len(subtitle_body.strip())} 字符）")

    if problems:
        log(f"✗ 自检未通过（{len(problems)} 项）：")
        for p in problems:
            log(f"  - {p}")
        return 1
    log(f"✓ 自检通过：{path.name}（{len(bullets)} 条要点，{fm.get('status')}）")
    return 0
# ---------------------------------------------------------------- main


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B站视频 -> Obsidian 笔记（确定性环节）")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("fetch", help="抓元数据 + 字幕")
    p1.add_argument("url", help="视频完整 URL（推荐）或 BV 号")
    p1.add_argument("--out", required=True, help="工作目录（放 manifest/字幕/cookie 副本）")
    p1.set_defaults(func=cmd_fetch)

    p2 = sub.add_parser("write", help="组装并写入笔记")
    p2.add_argument("--work", required=True, help="fetch 产生的工作目录")
    p2.add_argument("--note-dir", help="笔记输出目录；缺省时读 BILI_VAULT/BILI_TARGET_DIR")
    p2.add_argument("--summary-file", help="判断性内容的 JSON（不给则写草稿）")
    p2.add_argument("--processed", help=".processed.json 路径，用于去重记录")
    p2.add_argument("--captured", help="覆盖捕获日期 YYYY-MM-DD")
    p2.set_defaults(func=cmd_write)

    p3 = sub.add_parser("verify", help="笔记结构自检")
    p3.add_argument("--note", required=True, help="笔记路径")
    p3.add_argument("--min-bullets", type=int, default=5)
    p3.set_defaults(func=cmd_verify)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except BiliNoteError as exc:
        log(f"✗ {exc}")
        return 1
    except KeyboardInterrupt:
        log("已中断")
        return 130


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
