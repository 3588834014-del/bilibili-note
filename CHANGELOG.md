# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [1.0.0] - 2026-10-03

首个公开版本。

### Added

- `SKILL.md`：触发词、两段式工作流（确定性脚本 + 判断性 agent 内容）、
  笔记模板、路径配置、故障排查表。
- `docs/FORMAT.md`：格式契约文档，说明哪些输出形状被下游归档管线约束。
- `scripts/bili_note.py`：确定性环节 CLI
  - `fetch` — 归一化 cookie 后用 yt-dlp 取元数据与 `ai-zh` 字幕（只拉字幕）
  - `write` — 组装笔记、写入 vault、维护 `.processed.json` 去重记录
  - `verify` — 笔记结构自检
- `scripts/install.py`：安装到 dsh 用户 skill 目录，支持 `--dry-run`。
- `tests/`：42 个离线单元测试，无需网络与 cookie。
- `.github/workflows/ci.yml`：Linux + Windows × Python 3.8/3.10/3.12。

### 关键技术点

- **cookie 归一化**：浏览器扩展导出的 Netscape cookie 文件 domain 带前导点却标记为
  host-only，触发 Python `http.cookiejar` 的 `domain_specified == initial_dot`
  断言，导致 yt-dlp 拒绝加载整个文件。归一化后 AI 字幕才能取到。
- **两条 yt-dlp 命令必须分开**：`--dump-json` 会抑制字幕写出，合并调用会静默丢字幕。
- **URL 归一化**：剥掉 `?spm_id_from=…&vd_source=…` 与 `?p=N`，支持 `b23.tv` 短链；
  否则要点跳链会被拼成含两个 `?` 的坏链接。
- **两套时间戳格式**：frontmatter `duration` 用 `H:MM:SS`，要点行标签用总分钟数
  （`[93:31]`），是与下游管线对齐的结果。

### 修正（开发期由测试套件与真实 E2E 发现）

- `verify` 原先用「笔记总字符数 < 800」判断笔记是否过小，会把字幕较短的短视频
  草稿误判为失败。改为只检查字幕正文是否为空壳。
- `norm_url` 原先对带追踪参数的链接原样返回，导致 `source` 带脏参数、跳链出现两个 `?`。
- `sanitize_filename` 原先未清洗 `#`，而 Obsidian 会把它当标题锚点截断 wikilink。
- **字幕语言协商曾把"有字幕"误判成"无字幕"并静默跳过整个视频。** 根因有两层：
  `--list-subs` 的表格解析会误吞问题，且 `--dump-json` 对 B站返回的
  `subtitles` / `automatic_captions` 两个键**都是空的**，无法据此判断字幕可用性。
  现在改为：解析 `--list-subs` 表格（只认表头之后的行），并在清单为空
  （B站对密集请求会临时限流）时**按优先级逐个语言真下载探测**，而不是直接放弃。
- `ytdlp_meta` 原先不带 cookie 请求元数据；B站对未登录请求的响应不稳定，现改为带 cookie。
- **`requires-python = ">=3.8"` 曾是假的。** `Path.write_text(..., newline=)` 是
  Python 3.10 才加入的参数，在 3.8 上直接 `TypeError`，意味着声明的最低版本根本跑不起来。
  现改用内部 `write_text_lf()`（基于 `open(newline="\n")`，3.8 起可用）统一写文件。
  这个 bug 由首次 CI 推送暴露，并新增 `TestPython38Compatibility` 静态守卫
  （AST 扫描 `write_text(newline=)` + 按最低版本做语法解析）防止复发。
