# bilibili-note

> **Turn Bilibili videos into structured Obsidian notes — subtitles only, never the video.**
>
> 中文说明见 [README.zh-CN.md](README.zh-CN.md)。

[![CI](https://github.com/3588834014-del/bilibili-note/actions/workflows/ci.yml/badge.svg)](https://github.com/3588834014-del/bilibili-note/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)

A small CLI — plus an optional agent skill — that turns a Bilibili video into a structured
Markdown note in your Obsidian vault. It downloads **only the subtitle file** (`--skip-download`),
cleans the timestamps, and writes a note with YAML front matter, a summary section, bullet
points carrying clickable `?t=` jump links, and the full transcript collapsed at the bottom.

The repetitive half (fetch, parse, format, verify) is a script. The judgment half (summary,
key points, tags) is left as a documented contract you or an agent fill in.

```
Bilibili URL ──▶ fetch ──▶ manifest.json ──▶ [you / agent] ──▶ content.json ──▶ write ──▶ note.md
                  │         subtitles +                      summary, bullets,       │
                  │         timestamped segments             tags                    │
                  └── yt-dlp, subtitles only ────────────────────────────────────────┘
```

## Why this one

- **Subtitles only.** `--skip-download` is mandatory, and the tool verifies no media file
  (`.mp4/.flv/.m4a/.part`) landed in the working directory. No reverse-engineering, no
  private APIs — just `yt-dlp`.
- **Multi-part videos are merged.** For an anthology (B站 分P 合集) it fetches every part's
  subtitles and rebases the timestamps onto one continuous timeline. Verified on a real
  6-part anthology: **2:10:32 → 2807 segments / 33214 characters**, matching an independent
  concatenation segment-for-segment. A part with no subtitles is skipped **while keeping its
  time slot**, so later timestamps stay correct.
- **It refuses to produce garbage.** If two parts yield byte-identical subtitles, it errors out
  instead of silently emitting a note whose content is one part repeated N times. (That exact
  bug shipped during development and was caught by this guard.)
- **No subtitles? It skips and tells you.** It never fabricates content or writes an empty note.

## Install

No dependencies — standard library only. `yt-dlp` is an **external command**, not a Python package.

### As a CLI

```bash
pipx install git+https://github.com/3588834014-del/bilibili-note.git
# or
git clone https://github.com/3588834014-del/bilibili-note.git
cd bilibili-note && python -m pip install .
```

Then:

```bash
bili-note --version
bili-note note "https://www.bilibili.com/video/BVxxxxxxxxx" \
    --note-dir ~/MyVault/Sources/Videos
```

### As an agent skill (DeepSeek Harness)

```bash
python scripts/install.py            # installs to ~/.agents/skills/bilibili-note
python scripts/install.py --dry-run  # show what would be copied
```

The skill then appears in the dsh skill catalog and can be triggered with phrases like
「B站笔记」. Installing the skill does **not** install the CLI and vice versa; they share the
same implementation (`scripts/bili_note.py`).

### Requirements

| Requirement | Notes |
|---|---|
| Python 3.8+ | stdlib only |
| [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) | `pip install -U yt-dlp` — the **only** download channel |
| Bilibili login cookies | Required for AI subtitles; see below |

## Cookies (required)

Bilibili's AI-subtitle endpoint needs an authenticated session.

1. Install the **Get cookies.txt LOCALLY** browser extension.
2. Log into [bilibili.com](https://www.bilibili.com).
3. Export `cookies.txt`.
4. Put it in your working directory, or point at it:

```bash
export BILI_COOKIES=/path/to/cookies.txt      # Linux / macOS
$env:BILI_COOKIES = "C:\path\cookies.txt"     # PowerShell
```

Videos where the uploader supplied their own subtitles may work without this.

### ⚠️ Why you can't feed the export straight to yt-dlp

This is the single most surprising thing in this repository. That extension writes lines like:

```
.bilibili.com	FALSE	/	FALSE	1819635894	SESSDATA	xxxxx
```

The domain carries a leading dot, yet the host-only flag in column 2 is `FALSE`. Python's
`http.cookiejar` asserts:

```python
assert domain_specified == initial_dot
```

The mismatch raises, and yt-dlp refuses to load the **entire file**:

```
ERROR: invalid Netscape format cookies file '...': '.bilibili.com\tFALSE\t/...'
```

The practical symptom is nasty: subtitles never arrive, and the error looks like
*"this video has no subtitles."* The fix is normalization — strip the leading dot and set the
flag to `FALSE` (a host-only cookie is still sent to `api.bilibili.com` and friends, so the
session is preserved). `normalize_cookies()` does this, plus filters to the `bilibili` domain
and de-duplicates by `domain/path/name`. You never have to do it by hand.

## Output directory

Nothing is hard-coded. Pick either:

```bash
bili-note note "<URL>" --note-dir "<vault>/Sources/Videos"    # explicit

export BILI_VAULT="/path/to/MyVault"                           # or environment
export BILI_TARGET_DIR="Sources/Videos"                        # optional, this is the default
```

> Deliberately no built-in default vault path: hard-coding one "looks right" path silently
> creates an empty directory on someone else's machine, and notes vanish into it.

## Usage

```bash
# fetch metadata + subtitles (subtitles only, never the video)
bili-note fetch "<URL>" --out ./work

# write the note straight from an existing work dir
bili-note write --work ./work --note-dir "<vault>/Sources/Videos" --content-file content.json

# one shot: fetch + write
bili-note note "<URL>" --note-dir "<vault>/Sources/Videos" [--content-file content.json]

# structural self-check
bili-note verify --note "<note path>"

# environment self-check: cookies / yt-dlp / output dir (no network)
bili-note selftest
```

`selftest` answers the question you will actually ask — *"why did I get no subtitles?"* —
before you blame the code:

```
  ✓ cookie file: /home/me/cookies.txt
  ✓ has SESSDATA login: ...
  ✓ yt-dlp: /usr/local/bin/yt-dlp
  ✗ output dir: not configured (pass --note-dir, or set BILI_VAULT/BILI_TARGET_DIR)
```

`fetch` walks you through each step:

```
✓ cookie normalized: ... (33 bilibili cookies)
✓ metadata: <title> (<uploader>) | 12:34
  subtitle languages: danmaku, ai-zh, ai-en
✓ subtitles: ai-zh, 134 segments, 1625 chars
✓ manifest: /path/to/work/manifest.json
```

### `content.json` — the judgment half

```json
{
  "summary": "One sentence, 60-120 chars, conclusion first.",
  "body": "200-400 chars covering the argument and the conclusion.",
  "tags": ["认知", "人文"],
  "cards": ["CC_example"],
  "bullets": [
    {"time": 0,  "title": "Short heading", "text": "One sentence."},
    {"time": 23, "title": "Short heading", "text": "One sentence."}
  ]
}
```

`time` is **seconds**; the tool renders the `MM:SS` label and the jump link for you.
`title` and `cards` are optional. Omit `--content-file` entirely to get a draft note with
placeholders and `status: draft`.

## What a note looks like

```markdown
---
title: Video title
source: https://www.bilibili.com/video/BVxxxxxxxxx
author: Uploader
captured: 2026-01-01
duration: "12:34"
type: video-note
status: completed
needs_summary: false
tags: ["认知", "人文"]
---
> [!info] 视频信息
> **UP主**：Uploader ｜ **时长**：12:34 ｜ [原视频](https://www.bilibili.com/video/BVxxxxxxxxx)

## 一句话摘要
…
## 核心要点
|- [00:00](https://www.bilibili.com/video/BVxxxxxxxxx/?t=0s) **Heading**：One sentence.
|- [00:23](https://www.bilibili.com/video/BVxxxxxxxxx/?t=23s) **Heading**：One sentence.
## 总结
…
## 笔记标签
认知 ｜ 人文
## 原始字幕
> [!quote]- 完整字幕（点击展开）
> …cleaned transcript, no timestamps or indices
```

> The `|- ` prefix on bullet lines looks like a broken table row, and it is — deliberately.
> A downstream archive checker matches `^\|- \[[0-9]{2}:[0-9]{2}\]`, so standard `- ` list
> syntax would be rejected. All format constraints and their reasons live in
> [docs/FORMAT.md](docs/FORMAT.md).

## Tests

No network, no cookies required:

```bash
python -m unittest discover -s tests -v
```

**108 tests** covering SRT parsing and timestamp cleanup (LF/CRLF/BOM, multi-line cues,
over-1-hour offsets, HTML and ASS tags, literal `<` `>` in text, zero-width characters,
duplicate rolling captions, empty cues), both timestamp formats, bullet-line contract,
draft-literal contract, cookie normalization, URL normalization (tracking params and `?p=`),
anthology timestamp rebasing and subtitle-file selection, duplicate-part detection, on-disk
write and de-duplication, `verify` positive/negative cases, the CLI wiring, Python
minimum-version compatibility guards, and repository hygiene.

CI runs the same suite on Linux + Windows × Python 3.8/3.10/3.12 — see
[.github/workflows/ci.yml](.github/workflows/ci.yml).

## Known limitations

- **Bilibili only.** Other yt-dlp-supported sites are out of scope.
- **Multi-part anthologies are merged and take a while.** Two requests per part; a 2-hour
  6-part anthology takes roughly 2 minutes. Pass `?p=6` to grab a single part instead.
  Jump links point at `{anthology URL}/?t={cumulative}s`, so they are exact when playing the
  anthology as a whole but **not** when you open one part standalone. That is inherent to
  merging, not a fixable bug.
- **Depends on Bilibili's AI subtitles.** Videos without subtitles (and without
  uploader-supplied ones) are skipped. No speech-to-text — this tool deliberately does not
  pull in ffmpeg/whisper.
- **AI subtitles contain recognition errors.** `ai-zh` is machine-generated and regularly
  mis-hears or drops words. The rule this project follows: quotes are copied verbatim and
  **not** silently corrected; anything recoverable from context is paraphrased *without*
  quote marks; anything unintelligible is dropped rather than guessed. See `SKILL.md` §2.6.
- **Timestamps over 1 hour use total minutes** (`[93:31]`), not `H:MM:SS` — deliberate, to
  match the existing corpus.
- ⚠️ **Videos longer than 99 minutes trip a downstream checker.** Because bullet labels are
  total minutes, labels past 100 minutes are **3 digits** (`[100:00]`, `[123:49]`). A validator
  using `^\|- \[[0-9]{2}:[0-9]{2}\]` only accepts 2 digits and will under-count, flagging long
  videos as "too few bullet points." Measured on the 2:10:32 anthology: **24%** of the timeline
  has 3-digit minutes, first at 100:00. Fix is to widen `{2}` to `{1,3}` downstream; this
  repo's checker does that. `verify` passes such notes but prints a warning.

## Contributing

Issues and PRs welcome. Read [docs/FORMAT.md](docs/FORMAT.md) before touching anything
format-related, and keep `python -m unittest discover -s tests` green — `TestDownstreamContract`
exists specifically to pin those contracts down.

## License

[MIT](LICENSE)
