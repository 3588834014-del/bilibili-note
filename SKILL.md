---
name: bilibili-note
description: 把 B站视频链接变成 Obsidian 结构化笔记。当用户说「B站笔记」「b站视频转笔记」「把这条B站视频存进 Obsidian」「视频入库」或直接给出一条 bilibili.com/video/BV... 链接要求整理成笔记时使用。负责：只拉字幕不拉视频、清理时间戳、按固定模板生成笔记并写入 Obsidian 库。
---

# B站视频 → Obsidian 笔记

把一条 B站视频链接固化成 vault 里的结构化笔记：拉字幕（**只拉字幕，绝不下载视频**）→ 清理时间戳 → 生成固定格式笔记 → 写入输出目录 → 结构自检。

流水线分两半，**不要混在一起做**：

| 环节 | 谁做 | 怎么保证可复现 |
|------|------|----------------|
| 下载、解析、格式化、落盘、自检 | 脚本 | `scripts/bili_note.py`，命令与参数写死 |
| 一句话摘要、核心要点、总结、标签 | **你（agent）** | 按本文件的输入输出规约生成 |

> **改格式之前先读 [`docs/FORMAT.md`](docs/FORMAT.md)**：frontmatter 的三处字面量、要点行的 `|- ` 行首、两种时间戳写法，都被下游归档管线反向约束着，改动会让笔记静默补不上内容。

---

## 0. 前置条件（开工前检查一次）

| 项 | 要求 | 检查方式 |
|----|------|----------|
| `yt-dlp` | **唯一允许的下载通道** | `yt-dlp --version` |
| B站登录 cookie | AI 字幕必须登录态 | 见下方「cookie」 |
| Python | 3.8+，只用标准库 | `python --version` |
| 输出目录 | Obsidian 库内已存在 | 见「输出路径配置」 |

**cookie**：按此优先级自动查找，无需手工传参——
1. 环境变量 `BILI_COOKIES` 指向的文件
2. 当前工作目录下的 `cookies.txt`

导出方式：浏览器装扩展 **Get cookies.txt LOCALLY** → 登录 bilibili.com → Export。只有 UP 主自行上传了字幕的视频才不需要登录态。

> **关键坑位（脚本已处理，但你必须知道原因）**：该扩展导出的行是 `.bilibili.com ⇥ FALSE ⇥ …`，即 domain 带前导点、第 2 列却是 host-only 标志。Python 的 `http.cookiejar` 有断言 `domain_specified == initial_dot`，因此 **yt-dlp 会直接报 `invalid Netscape format cookies file` 拒绝加载**。脚本会先归一化（去前导点 + `FALSE`）写成临时文件再喂给 yt-dlp。**不要**试图把原始导出文件直接 `--cookies` 给 yt-dlp。

---

## 1. 抓取（确定性，跑脚本）

```bash
python <skill-dir>/scripts/bili_note.py fetch "<视频完整URL>" --out <工作目录>
```

产出 `<工作目录>/manifest.json`，含 `bvid / url / title / uploader / duration / duration_hms / subtitle_lang / subtitle_text / segments[{time,text}]`。

**脚本内部固化的命令（不要改成别的）**：

```bash
# 元数据（此模式不下载、也不写字幕）
yt-dlp --dump-json --skip-download --no-warnings "<URL>"

# 字幕：只拉字幕，不拉视频
yt-dlp --cookies <归一化cookie> --skip-download \
       --write-subs --write-auto-subs \
       --sub-langs ai-zh --sub-format srt/best \
       --paths <工作目录> -o "%(id)s" --no-warnings "<URL>"
```

约束与理由：

- **`--skip-download` 是硬要求**：它让 yt-dlp 只写字幕文件。跑完确认工作目录里**没有** `.mp4/.flv/.m4a/.webm/.part`，出现即为失败信号。
- **两条 yt-dlp 命令必须分开跑**：`--dump-json` 会独占 stdout 且**抑制字幕写出**，合并调用会静默丢掉字幕。
- 字幕语言优先级 `ai-zh → ai-zh-Hans → ai-zh-CN → zh-CN → zh-Hans → zh`。先 `--list-subs` 取真实清单再挑，不要盲传 `ai-zh`。
- `-o "%(id)s"` 用纯 BV 号做文件名，避开中文标题在终端编码上的坑。
- 输入链接可以带追踪参数（`?spm_id_from=…&vd_source=…`）或 `b23.tv` 短链，脚本会归一到 `https://www.bilibili.com/video/{BV}`。**必须归一**：否则要点跳链会被拼成含两个 `?` 的坏链接。
- **`?p=N` 会被保留**（它不是追踪参数，是分 P 选择器）。

> **多 P 视频的坑（务必核对！）**：yt-dlp 对 `https://www.bilibili.com/video/BVxxx`
> **默认只取第 1 个分 P**。遇到分 P 合集（视频页有 p1/p2/…）时：
> 1. `manifest.json` 的 `duration` / `title` 会只反映那一个分 P；
> 2. 要指定分 P，让用户提供带 `?p=N` 的链接；
> 3. **本 skill 不会把所有分 P 拼成一篇笔记**。若用户期望整篇合集笔记，明说做不到，
>    不要拿 p1 的字幕冒充整个合集。
>
> 动手前扫一眼 `fetch` 输出的时长与标题：如果用户描述的是"一个 2 小时的教程"而
> `duration` 只有 7 分钟，那就是撞上这个坑了。

**失败处理**：拿不到字幕 → **跳过该视频，不生成空笔记**，向用户报告原因后继续处理队列里的下一条。区分两种原因：视频本身没字幕 / cookie 登录态失效（后者提示重新导出）。

**备用通道**：若归一化后 yt-dlp 仍不可用（版本过旧、接口变动），可自备一个直调接口的提取脚本作为退路。它是备选，不是默认；默认一律走 yt-dlp。

---

## 2. 生成判断性内容（你来做）

读 `manifest.json` 的 `subtitle_text`（全文）和 `segments`（带秒级时间戳）。**只依据字幕内容，不要引入视频里没有的信息。**

### 2.1 一句话摘要

- 1 句，60–120 字，结论前置。
- 回答「这个视频讲了什么、结论是什么」，不是「视频讨论了……」。

### 2.2 核心要点

- **5–10 条**，按视频讲述顺序（不是按时间戳机械升序）。
- 每条严格用这个格式，`time` 取该要点在 `segments` 里对应的秒数：

```
|- [MM:SS](<视频URL>/?t=<秒数>s) **小标题**：一句话说明
```

> **行首 `|- ` 是硬要求，不要"修正"成标准 markdown 列表 `- `。**
> 下游归档管线的校验器用的正则是 `^\|- \[[0-9]{2}:[0-9]{2}\]`，只认这一种形状。
> 写成 `- [...]` 会被判「要点不足」。详见 `docs/FORMAT.md`。

- 约束：
  - 标签 `MM:SS` 零填充（`[00:20]`）。
  - **超过 1 小时不做小时进位，用总分钟数**：93 分 31 秒写 `[93:31]`，不是 `[1:33:31]`。
  - 跳链必须是 `?t=<整数>s`，且 `<视频URL>` 后要有一个 `/`：`https://www.bilibili.com/video/BVxxx/?t=20s`。
  - `**小标题**` 4–12 字，可省略；说明 20–60 字。
  - 时间戳必须真的落在字幕里出现该内容的时刻，不要均分臆造。
- 冒号用全角 `：` 分隔小标题与说明。

### 2.3 总结正文

- 200–400 字，讲清论证链条 + 最终结论。
- 不要写成要点罗列，要是连贯段落。

### 2.4 笔记标签

- 从 vault 根目录 `_tags.md` 的白名单里选 **1–3 个**，必须逐字命中白名单。
- 若目标库没有 `_tags.md`，向用户确认可用的标签集合，不要自创。
- 另可带命名空间标签 `类型/…`、`状态/…`、`领域/…`。

### 2.5 相关卡片（可选）

vault 中已有 `CC_*.md` 概念卡片且确实对应时，列其文件名（不带扩展名）；没有就省略整个 `## 相关卡片` 章节。

### 2.6 交给脚本

把上述内容写成 JSON：

```json
{
  "summary": "一句话摘要",
  "body": "总结正文",
  "tags": ["认知", "人文"],
  "cards": ["CC_示例"],
  "bullets": [
    {"time": 0,   "title": "小标题一", "text": "一句话说明"},
    {"time": 23,  "title": "小标题二", "text": "一句话说明"}
  ]
}
```

`time` 填**秒数**（整数即可），脚本负责渲染成 `MM:SS` 和跳链——不要自己拼时间戳字符串。
`title` 可省略（省略后该条就没有 `**小标题**`）。

**关于 AI 字幕的识别错误**：B站的 `ai-zh` 字幕由语音识别产生，常有错字、漏字、断句错误。处理边界是——
- **不修正引语**：引用字幕原句时按识别结果抄，不要"顺手改对"。
- **不编造**：能用上下文合理还原的，按语义概括而**不要写成引号引语**。
- 完全不可解的部分**直接丢掉不写**，不要猜。
- 若字幕质量明显影响理解，在交付时向用户说明，让用户决定是否换用纠正版引语。

---

## 3. 落盘（确定性，跑脚本）

```bash
# 完成态：判断性内容已备好
python <skill-dir>/scripts/bili_note.py write \
  --work <工作目录> --note-dir <输出目录> \
  --summary-file <summary.json> --processed <输出目录>/.processed.json

# 草稿态：只落字幕骨架，判断性内容留占位符待后续补全
python <skill-dir>/scripts/bili_note.py write \
  --work <工作目录> --note-dir <输出目录>
```

**文件名规则**：`B站 {标题}.md`。非法字符 `< > : " / \ | ? *`、控制字符**以及方括号 `[ ]` 和 `#`** 一律替换为空格——方括号是 Obsidian wikilink 的定界符，`#` 会被当作标题锚点把链接截断。同名但 BV 号不同时追加 ` {BV号}` 避免互相覆盖。

**去重**：按 BV 号查 `<输出目录>/.processed.json`（JSON 字符串数组）。已在其中说明处理过，默认跳过并向用户说明；用户明确要求重跑时才覆盖。

**frontmatter 模板**（逐字段写死）：

```yaml
---
title: {视频标题}
source: https://www.bilibili.com/video/{BV号}   # 必须是完整 URL，不要只写 BV 号
author: {UP主}
captured: {YYYY-MM-DD}
duration: "{M:SS}"                              # 带引号，避免被解析成时间
type: video-note
status: draft                                   # 草稿 draft；已补全则 completed
needs_summary: true                             # 草稿 true；已补全 false
tags: []                                        # 留空，交给后续归档管线填
---
```

> `source` 必须是完整 URL 且不带追踪参数：只写裸 BV 号会让 Obsidian 里不可点击，带 `?spm_id_from=…` 会让后续管线按 URL 正则匹配不到，也会毁掉要点跳链。
>
> `tags` 字段**留空**（`[]`）：本 skill 生成草稿时不猜标签，标签由后续归档环节确定。

**笔记正文骨架**：

```markdown
> [!info] 视频信息
> **UP主**：{UP主} ｜ **时长**：{M:SS} ｜ [原视频]({URL})

## 一句话摘要

{摘要}

## 核心要点

|- [MM:SS]({URL}/?t={秒}s) **小标题**：说明

## 总结

{正文}

## 笔记标签

{标签}

## 相关卡片

- [[CC_xxx]]

## 原始字幕

> [!quote]- 完整字幕（点击展开）
> {全文字幕，单行}
```

---

## 4. 自检（不做完不算交付）

```bash
# 离线测试套件：不联网、不读 cookie
python -m unittest discover -s <skill-dir>/tests -v

# 笔记结构自检
python <skill-dir>/scripts/bili_note.py verify --note "<笔记路径>" --min-bullets 5
```

`verify` 检查：frontmatter 九字段齐全且顺序一致、`source` 是规范完整 URL、`tags` 是数组、无 BOM、`status: draft` 与占位符一致性、要点数 ≥5 且每条跳链含 `?t=`、必备章节 + 视频信息 callout + 字幕折叠块、字幕正文非空。

**自检通过后还要人工过一眼**：字幕是否真的拉到了（非空、非占位）、时间戳清理是否干净（无 `00:00:00,080 -->` 这类残留）、要点时间戳是否对得上内容、落盘目录是否正确。把笔记路径给用户看，等验收。

---

## 5. 输出路径配置

笔记写入 **Obsidian 库内的输出目录**，本 skill 不写死库路径，按此顺序解析并在动手前跟用户确认一次：

1. `--note-dir` 显式指定（最明确，推荐）
2. 环境变量 `BILI_VAULT`（库根）+ `BILI_TARGET_DIR`（默认 `Sources/Videos`）

> 脚本**不会**内置任何默认库路径。硬编码一个"看起来对"的路径只会在别的机器上静默创建出空目录，笔记就此失踪。动手前先确认一次输出目录。

---

## 6. 禁止事项

- **不写任何逆向/爬虫代码**。下载只走 yt-dlp。签名算法、WBI 校验、私有接口一律不碰。
- **不下载视频/音频**。只允许字幕文件落地。
- 不把原始字幕原样丢进 Obsidian 就交付——时间戳清理和结构组装是必须步骤。
- 不在字幕缺失时硬造内容，也不要生成没有字幕的空笔记。
- 不要用 PowerShell `Set-Content -Encoding UTF8` 写清单文件（会带 BOM 污染首行 URL）；需要写文本时用 `[System.IO.File]::WriteAllLines` + `UTF8Encoding($false)`，或直接交给脚本。

---

## 7. 常见故障

| 现象 | 原因 | 处理 |
|------|------|------|
| `invalid Netscape format cookies file` | 直接把浏览器导出文件给了 yt-dlp | 走脚本的归一化步骤，不要绕过 |
| 元数据拿到了但没字幕文件 | `--dump-json` 与 `--write-subs` 合并调用 | 两条命令分开跑 |
| `该视频没有中文字幕` | 视频本身无字幕，或登录态失效 | 无字幕→跳过并报告；失效→重新导出 cookie |
| 工作目录出现 `.mp4/.part` | 命令漏了 `--skip-download` | 立即停止，检查命令 |
| 笔记里时间戳是 `00:00:00,080 -->` | SRT 未清理 | 用 `parse_srt`，不要手工正则拼 |
| 要点跳链打不开 | URL 带了追踪参数，拼出两个 `?` | 先 `norm_url` 再拼跳链 |
| 笔记"落错目录" | 输出目录配置错误 | 显式传 `--note-dir` |
