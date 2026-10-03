# [分享创造] bilibili-note：把 B站视频变成 Obsidian 笔记（只拉字幕，不下载视频）

> 发布平台：V2EX / Obsidian 中文论坛。语气按开发者分享写，不推销。
> `https://github.com/3588834014-del/bilibili-note` 是 GitHub 链接位，发布前统一替换。
> 正文里的坑、数字、限制都来自仓库 README.md / SKILL.md / docs/FORMAT.md / CHANGELOG.md。

---

## 正文

### 背景

我看 B站知识区视频的流程一直是：看 → 暂停 → 手抄时间戳 → 回 Obsidian 排版。视频越长越不想整理，最后收藏夹越堆越多。

于是写了个工具：给一条 B站链接，产出 vault 里一条结构化笔记——frontmatter + 视频信息 + 一句话摘要 + 带时间戳跳转的核心要点 + 总结 + 标签 + 默认折叠的完整字幕。

设计上把流程切成两半，两边不混：

| 环节 | 谁做 | 怎么保证可复现 |
|------|------|----------------|
| 下载、解析、格式化、落盘、自检 | 脚本 `scripts/bili_note.py` | 命令与参数写死 |
| 一句话摘要、核心要点、总结、标签 | agent | SKILL.md 里的输入输出规约 |

这么切的原因是：脚本负责可复现的机械环节，agent 只负责需要判断的内容。判断性内容没法写死，机械环节也不该让模型自由发挥。

**形态上是一个 CLI**（`bili-note`），顺带也能当 agent skill 用——两者共用同一份实现，不会出现行为分叉。

### 怎么做的

```bash
# 一行装好
pipx install git+https://github.com/3588834014-del/bilibili-note.git

# 一步到位：抓字幕 + 写笔记
bili-note note "<视频完整URL>" --note-dir "<vault>/Sources/Videos"

# 或者分两步：先只抓字幕，供 agent 读 manifest
bili-note fetch "<视频完整URL>" --out ./work
bili-note write --work ./work --note-dir "<vault>/Sources/Videos" --content-file content.json

# 结构自检
bili-note verify --note "<笔记路径>"
```

不想装也可以直接跑仓库里的脚本：`python scripts/bili_note.py fetch ...`（子命令同名）。

`fetch` 内部固化的两条 yt-dlp 命令（不要改成别的）：

```bash
# 元数据（此模式不下载、也不写字幕）
yt-dlp --dump-json --skip-download --no-warnings "<URL>"

# 字幕：只拉字幕，不拉视频
yt-dlp --cookies <归一化cookie> --skip-download \
       --write-subs --write-auto-subs \
       --sub-langs ai-zh --sub-format srt/best \
       --paths <工作目录> -o "%(id)s" --no-warnings "<URL>"
```

不写任何逆向/爬虫代码，签名算法、WBI 校验、私有接口一律不碰，下载只走 yt-dlp。`--skip-download` 是硬要求：跑完工作目录里不应该有 `.mp4/.flv/.m4a/.webm/.part`，出现就是失败信号。

### 踩到的坑

#### 1. 浏览器导出的 cookie 文件，yt-dlp 拒收（这个坑值得单独开一帖）

浏览器扩展「Get cookies.txt LOCALLY」导出的行长这样：

```
.bilibili.com	FALSE	/	FALSE	1819635894	SESSDATA	xxxxx
```

domain 带前导点 `.`，但第 2 列的 host-only 标志是 `FALSE`。Python 的 `http.cookiejar` 内部有一条断言：

```python
assert domain_specified == initial_dot
```

两者不一致 → 抛 `LoadError` → yt-dlp 直接拒绝加载整个文件：

```
ERROR: invalid Netscape format cookies file '...': '.bilibili.com\tFALSE\t/...'
```

这个坑最恶心的地方在表象：**字幕永远拿不到，而报错看起来像"这个视频没有字幕"。** 而"字幕拿不到"在开发期其实对应三个互不相干的原因——B站对密集请求会临时限流、字幕语言协商曾把"有字幕"误判成"无字幕"、以及这行 cookie——所以它特别容易被归错因。

解法是归一化：**去掉 domain 的前导点，把标志置为 `FALSE`**。host-only cookie 仍然会发给 `api.bilibili.com` 等子域，登录态不丢。`normalize_cookies()` 做的就是这个，顺带做了两件事：过滤出 bilibili 域（导出文件通常混着其他站点的 cookie）、按 domain/path/name 去重，然后写临时文件再喂给 yt-dlp。

结论：**不要试图把原始导出文件直接 `--cookies` 给 yt-dlp。**

#### 2. 两条 yt-dlp 命令必须分开跑

`--dump-json` 会独占 stdout，并且**抑制字幕写出**。合并成一条调用不会报错，只是静默地没有字幕文件——你会以为这视频没字幕。现在元数据一条、字幕一条，分开跑。

#### 3. 合集里 6 个分 P 都读到了同一份字幕

`ytdlp_subs()` 原先用 glob `{bvid}*.srt` 挑产物。合集下 `BV1x_p1.srt … BV1x_p6.srt` 全部命中，`sorted()` 之后永远取到 `_p1`——于是 6 个分 P 合成同一份字幕（954 段，全部重复），而时长、时间戳、笔记结构看起来全都正常。

这个属于"合成结果看起来正常、实则内容全错"，比崩溃难查得多。现在改成按 `<bvid>_pN.{lang}.srt` 精确匹配，再用"下载前后新增/更新的文件"兜底，并加了 `find_duplicate_part_texts()`：两个分 P 的字幕正文完全相同就直接报错拒绝生成。

#### 4. `?p=N` 被当成追踪参数剥掉了

归一化 URL 的时候我顺手把查询参数都清了，`?p=6` 一起没了——多 P 视频静默退化成第 1 个分 P，标题、时长、字幕全部指向错误的视频，而且不报错。`?p=` 是分 P 选择器不是追踪参数，现在保留，其余查询参数照剥。

#### 5. 声明的 Python 最低版本是假的

`requires-python = ">=3.8"`，但代码里用了 `Path.write_text(..., newline=)`——这是 Python 3.10 才加入的参数，在 3.8 上直接 `TypeError`。也就是说声明的最低版本根本跑不起来。第一次把 CI 推上去就炸了。现在改用内部 `write_text_lf()`（基于 `open(newline="\n")`，3.8 起可用）统一写文件，并加了 `TestPython38Compatibility` 静态守卫（AST 扫描 `write_text(newline=)` + 按最低版本做语法解析）防止复发。

### 几个刻意的设计（看着奇怪，其实是被下游约束的）

- **要点行以 `|- ` 开头**，看着像坏掉的 markdown 表格。它是故意的：下游归档管线的校验器用的正则是 `^\|- \[[0-9]{2}:[0-9]{2}\]`，写成标准列表 `- [...]` 会被判「要点不足」。格式契约写在 `docs/FORMAT.md`。
- **草稿 frontmatter 的三处字面量不能动**：归档替换脚本用的是字符串替换而不是 YAML 解析（`"status: draft"` → `"status: completed"`、`"needs_summary: true"` → `false`、`"tags: []"` → `'tags: ["a", "b"]'`），加引号或改成 `tags:` 空值都会让笔记**静默地补不上内容**。
- **`duration` 必须带双引号**：`"38:46"` 不加引号会被 YAML 1.1 当成六十进制整数。
- **同一个秒数，两套时间戳写法**：frontmatter 的 `duration` 做小时进位（`2:10:35`），要点行标签用**总分钟数**不进位（93 分 31 秒写 `[93:31]`，不是 `[1:33:31]`）。这是跟已有语料对齐的结果，不是 bug。

### 已知限制

先说清楚，免得装完发现不合适：

- **只支持 B站。** yt-dlp 支持的其他站点不在设计范围内。
- **依赖 B站的 AI 字幕。** 视频没有字幕（且 UP 主未上传）时跳过，不做语音转写。要转写属于另一条工具链，这个项目刻意不引入 ffmpeg/whisper 依赖。
- **AI 字幕有识别错误。** `ai-zh` 由语音识别产生，错字、漏字、断句错误都常见。处理边界是：引用字幕原句时照抄识别结果，不"顺手改对"；能用上下文合理还原的按语义概括，**不写成引号引语**；完全不可解的直接丢弃，不猜。字幕质量明显影响理解时会向用户说明。
- **超过 99 分钟的视频会触发下游校验器漏判。** 要点标签是总分钟数，所以 100 分钟之后是 **3 位数**（`[100:00]`、`[123:49]`）。实测 2:10:32 的合集里 **24%** 的时刻如此，最早出现在 100:00。但下游校验器的正则是 `^\|- \[[0-9]{2}:[0-9]{2}\]`，**只认 2 位分钟**，于是长视频笔记会被误判成「要点不足」。这不是格式错误——两边的产出者是同一个格式约定；修法是下游把 `{2}` 放宽为 `{1,3}`（`bilibili2obsidian` 侧的 `_verify_batch.py` 与 `_sort_bullets.py` 已改）。本仓库的 `verify` 对这类笔记**放行但打印警告**，避免下游静默漏判。如果你在自己的 vault 里用类似校验，记得放宽。
- **合集跳链的固有精度问题。** 时间戳是按累计时长平移拼接的，B站整 P 连续播放时累计时间轴成立；但若观众**单独打开某个分 P**，链接不会精确落到那个分 P 的位置。这是合集的固有限制，不是可以修掉的 bug。
- **抓取速度**：每个分 P 一次元数据 + 一次字幕请求，2 小时合集约 2 分钟；某分 P 无字幕时跳过但保留其时间占位，时间轴不错位。
- **不接受硬编码库路径**：脚本不内置任何默认 vault 路径，必须显式传 `--note-dir` 或用 `BILI_VAULT` + `BILI_TARGET_DIR`。硬编码一个"看起来对"的路径只会在别的机器上静默创建出空目录。

### 测试

离线测试套件，不需要网络也不需要 cookie：

```bash
python -m unittest discover -s tests -v
```

CI 在 Linux + Windows × Python 3.8 / 3.10 / 3.12 上跑同一套。测试里 `TestDownstreamContract` 就是拿来钉死格式契约的——改格式相关的东西之前请先读 `docs/FORMAT.md`，并确保套件仍然全绿。

### 其他

MIT。欢迎 issue 和 PR。

仓库：https://github.com/3588834014-del/bilibili-note

---

## 发帖备注（不随正文发布）

- 标题备选：
  - `[分享创造] 把 B站视频变成 Obsidian 笔记，顺手踩了个 yt-dlp 的 cookie 坑`
  - `[分享创造] bilibili-note：只拉字幕不下载视频的 B站转笔记工具`
  - `[程序员] 字幕拉不到的真凶：一个 Netscape cookie 格式坑`
- 发 V2EX 建议：正文直接发「踩到的坑」的第 1 条作为引子，其余内容放在回复里展开，符合 V2EX 的讨论习惯；「已知限制」一节保留在正文，能挡掉一批"为什么不支持 XX"的回复。
- 发 Obsidian 中文论坛建议：把「几个刻意的设计」提到靠前的位置，那边对格式约定更感兴趣。
- 事实核查清单（发布前对照仓库确认一遍）：2:10:32 / 6 分 P / 2807 段 / 33214 字 / 24% / 100:00 / 954 段重复 / 2 小时合集约 2 分钟 / 正则 `^\|- \[[0-9]{2}:[0-9]{2}\]`。
