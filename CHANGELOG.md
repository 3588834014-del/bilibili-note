# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [1.1.0] - 2026-10-03

把项目从「一个 dsh skill」扩成「CLI + skill」，并补上英文文档。
动机：skill 的可用受众 = 装了 dsh 的人 ∩ 用 B站 ∩ 用 Obsidian，交集太小；
同一份实现套个 CLI 入口就能覆盖所有命令行用户。

### Added

- **独立 CLI**（`bili-note`）：`pip install .` 后可用，也可 `python -m bili_note_cli.cli` 免安装直接跑。
  子命令：`note`（抓取+写笔记一步到位）、`fetch`、`write`、`verify`。
  CLI 是薄壳，唯一实现仍是 `scripts/bili_note.py`——两者共用同一份代码，不会出现行为分叉。
- `bili_note_cli/cli.py` + `pyproject.toml` 的 `[project.scripts]` 与控制台入口。
  用 `package-dir` 把 `scripts/bili_note.py` 映射成顶层模块 `bili_note` 打包，
  避免为了打包而挪动文件（挪了 skill 就找不到它）。
- **英文 README**（`README.md`），原中文 README 保留为 `README.zh-CN.md`，两边互相链接。
- CI 新增 `package` job：在 ubuntu + windows 上真跑 `pip install .`，验证控制台脚本能起来、
  模块可导入。单测只能覆盖「仓库内直接跑」，装不上是只有这条路径才暴露的问题。
- 测试 86 → 112：新增 `tests/test_cli.py`（CLI 接线、参数契约、子进程 smoke、
  打包配置一致性、skill 安装脚本的文件过滤）。

### 修正

- `scripts/install.py` 会把 `pip install` 生成的 `*.egg-info` 一起复制进 skill 目录。
  现在过滤构建产物（`*.egg-info` / `*.pyc` / `build` / `dist` 等）。
- 仓库卫生检查会把构建产物也当成源码来查 CRLF，导致「构建过之后再跑测试」必然失败。
  现在跳过 `.gitignore` 已排除的生成目录。
- CHANGELOG 内部矛盾：「关键技术点」一节写"剥掉 `?p=N`"，但「修正」一节写"现在保留 `?p=`"。
  按现行行为统一为**保留**。

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
- **URL 归一化**：剥掉 `?spm_id_from=…&vd_source=…` 等追踪参数，**保留 `?p=N`**
  （分 P 选择器，不是追踪参数），支持 `b23.tv` 短链；
  否则要点跳链会被拼成含两个 `?` 的坏链接。
- **两套时间戳格式**：frontmatter `duration` 用 `H:MM:SS`，要点行标签用总分钟数
  （`[93:31]`），是与下游管线对齐的结果。

### Added

- **多 P 合集支持**：识别 `n_entries` / `playlist_id` 自动判定合集，逐分 P 抓取字幕
  并按累计时长把时间戳平移成一条连续时间轴。实测 `BV1SYNTH0001`（6 分 P）：产出
  2:10:32 / 2807 段 / 33214 字，与独立拼接的结果逐段一致。传 `?p=N` 则只取该分 P。
  无字幕的分 P 跳过但保留其时间占位，时间轴不错位。
- `find_duplicate_part_texts()` 完整性守卫：若两个分 P 的字幕正文完全相同则报错
  拒绝生成，拦截"每页都读到同一份字幕"这类合成后看似正常、实则内容全错的故障。
- 长视频时间戳的真实数据验证：确认要点标签在超过 99 分钟后是 3 位总分钟数
  （`[100:00]`、`[123:49]`），与 vault 语料里唯一的 3 位标签逐字一致。
- `TestDownstreamRegexLimitation`：把"下游正则只认 2 位分钟、长视频会被判要点不足"
  这一限制显式锁进测试，并验证放宽为 `[0-9]{1,3}` 后两条路都通过。
- `verify` 对含 3 位分钟标签的笔记**放行但打印警告**，避免下游静默漏判。

### 修正（开发期由测试套件与真实 E2E 发现）

- **合集里每个分 P 都读到了同一份字幕。** `ytdlp_subs()` 用 glob `{bvid}*.srt`
  挑产物，合集下 `BV1x_p1.srt … BV1x_p6.srt` 全部命中，`sorted()` 后永远取到 `_p1`；
  于是 6 个分 P 合成了同一份字幕（954 段，全部重复），而时长、时间戳、笔记结构
  看起来都正常。现改为按 `sub_filename_stem()` 精确匹配 `<bvid>_pN.{lang}.srt`，
  并用"下载前后新增/更新的文件"兜底，配合上述重复检测守卫。
- **`?p=N` 曾被当成追踪参数剥掉**，导致多 P 视频静默退化成第 1 个分 P（标题、时长、
  字幕全部指向错误的视频）。现在保留 `?p=`，其余查询参数照旧剥除。

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
