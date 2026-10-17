---
name: douyin-grab
description: 抖音图文（图集 + 文案）→ Obsidian 笔记。当用户说「抖音图文入库」「抓这条抖音」「把这条图文存进 Obsidian」，或直接给出一条抖音分享口令/链接要求整理成笔记时使用。脚本负责解析、下载图片、写 meta.json、去重；agent 负责逐张读图、客观描述、按模板写笔记。
---

# 抖音图文 → Obsidian 笔记

把一条**抖音图文**（图集 + 文案）固化成 vault 里的素材笔记。视频、直播、纯文字都不走这条管线。

流水线分两半，**不要混在一起做**：

| 环节 | 谁做 | 怎么保证可复现 |
|------|------|----------------|
| 提链接、解析、下载图片、写 `meta.json`、去重 | 脚本 | `scripts/dy_grab.py` |
| 逐张读图、客观描述、按模板写笔记 | **你（agent）** | 按本文件的输入输出规约生成 |

> 脚本**不调用任何 AI / 大模型，不需要 API 密钥**。它只做确定性脏活。

---

## 0. 前置检查（开工前一次）

| 项 | 要求 | 检查方式 |
|----|------|----------|
| Python | 3.10+ | `python --version` |
| Git | 用于首次 clone 解析库 | `git --version` |
| 依赖 | `requests` | `python -m pip install -r <skill-dir>/requirements.txt` |
| 网络 | 可访问 GitHub / 抖音图片 CDN | 失败时看退出码 `4` |
| 库根 | 必须能定位到含 `.obsidian` 的目录 | 见下方「库根路径」 |

### 库根路径（先确认，再运行）

脚本需要知道图片写到哪里。按以下优先级解析：

1. 命令行 `--vault "<库根绝对路径>"`（**推荐，最明确**）
2. 环境变量 `DOUYIN_VAULT`
3. 从脚本目录向上自动探测 `.obsidian`

> **坑位**：本机 vault 名里 `Q` 和 `x` 之间是 **U+2006 SIX-PER-EM SPACE**，不是普通空格。手打会静默落到另一个目录。引用时让程序拼路径，不要手敲。

---

## 1. 跑抓取脚本（确定性）

```bash
python <skill-dir>/scripts/dy_grab.py --vault "<库根>" "<抖音分享口令>"
```

脚本会打印进度，**最后一行是 JSON 摘要**。只读最后一行 JSON，不要被中间日志干扰。

可选参数：

| 参数 / 环境变量 | 作用 |
|-----------------|------|
| `--vault PATH` / `DOUYIN_VAULT` | Obsidian 库根 |
| `--inbox NAME` / `DOUYIN_INBOX` | 笔记目录名（默认 `00_Inbox`，仅记录，脚本不写笔记） |
| `DOUYIN_ATTACH` | 图片目录（默认 `attachments/douyin`） |
| `DOUYIN_COOKIE_FILE` | `cookies.txt` 路径（提升风控下的成功率） |
| `DOUYIN_PORT` | 本地解析服务端口（默认 `8783`） |
| `DOUYIN_API_DIR` | 解析库 clone 目录 |

**cookie 安全**：`cookies.txt` 只放本地，**绝不要**写进笔记、提交到 Git 或贴进对话。仓库 `.gitignore` 已忽略 `cookies*.txt`。

---

## 2. 结果分流

### 情况 A：`status == "duplicate"`

之前抓过了。直接告诉用户：

> 这条之前已经抓过了，原始标题是：`<note 字段>`。

结束，**不要**重复下载、不要新建笔记。

### 情况 B：`status == "ok"`

拿到 `aweme_id`、`meta_path`、`images` 数量，继续第 3 步。

### 情况 C：退出码非 0

用一句人话解释，**不要硬凑笔记**：

| 退出码 | 含义 | 转达示例 |
|--------|------|----------|
| `2` | 口令里没链接 | 「这条口令里没找到链接，请重新复制完整口令」 |
| `3` | 解析失败 | 「抖音解析失败了，可能链接失效或触发风控，配置 cookie 再试」 |
| `4` | 网络错误 | 「连不上解析服务或抖音，请检查网络后重试」 |
| `5` | 非图文 | 「这条是视频/直播，本管线只支持图文」 |
| `6` | 未找到库根 | 「没定位到 Obsidian 库，请把库根路径传给我」 |

---

## 3. 打开 `meta.json`，逐张看图

路径：`<vault>/attachments/douyin/<aweme_id>/meta.json`，图片在同目录。

`meta.json` 字段：

```json
{
  "aweme_id": "7...",
  "title": "标题",
  "caption": "文案原文",
  "author": "作者昵称",
  "create_time": 0,
  "image_files": ["01.jpg", "02.jpg"],
  "failed_images": [],
  "source_url": "https://...",
  "grab_time": "YYYY-MM-DD HH:MM:SS"
}
```

**逐张**查看 `image_files` 里的图片，每张写 **1–3 句客观描述**：

- 只描述实实在在看到的东西：主体、场景、构图、画面里的文字。
- 不脑补、不夸张、不编故事。
- 图片可能带文字、弹窗、水印，如实描述。

> 识图方式：先用 `Read` 读图片。若当前模型不支持原生视觉（返回乱码/报错），按 `AGENTS.md` 走 `vision.js` fallback，把脚本输出当作识图结果。

---

## 4. 按模板写笔记

写入 `<vault>/<INBOX_DIR>`（默认 `00_Inbox`）。

文件名 = 标题清洗后 ≤ 30 字 + 日期后缀 `YYYY-MM-DD.md`。
清洗规则：`< > : " / \ | ? *`、控制字符、`[ ]`、`#` 替换为空格；连续空格压成一个；去掉首尾空格和句点。

```markdown
---
title: "（原文标题）"
source: "（source_url）"
author: "（作者昵称）"
date: YYYY-MM-DD
type: douyin-图文
status: inbox
tags: []
---

## 文案

（caption 原文，一字不改）

## 图片内容

![](../attachments/douyin/<aweme_id>/01.jpg)

- 图1：（描述）
- 图2：（描述）

> 抓取于 YYYY-MM-DD HH:MM
```

**硬性要求**：

- `tags: []` 必须是空数组，**不要填任何标签**。分类交给后续归档管线。
- `type: douyin-图文`、`status: inbox` 逐字照写。
- `source` 用完整 URL，不要只写短链或 ID。
- 图片路径相对**笔记所在目录**计算。笔记在 `00_Inbox`、图片在 `attachments/douyin/<id>/` 时，写 `../attachments/douyin/<id>/01.jpg`。
- 文案保留原文；有错别字也不要改，必要时可在对话里提醒，但不改笔记。
- 日期取 `grab_time` 的日期部分。

---

## 5. 收尾

回复用户在 vault 中的笔记位置（用 Obsidian 可点击的 wikilink），并报告：

- 抓到几张图、成功几张；
- 是否配置了 cookie；
- 若有失败图片，列出 `failed_images` 数量。

任何失败都用人话说明原因 + 下一步建议（重试 / 配置 cookie / 换一条），不要只贴英文报错。

---

## 6. 禁止事项

- 不处理视频、直播、纯文字；不下载视频/音频。
- 不修改脚本来绕过解析服务的风控；优先配置 cookie。
- 不把 `cookies.txt`、`processed.json`、`server.log`、`__pycache__` 提交到 Git。
- 不给笔记打标签、不移动笔记、不替用户做领域分类。
- 不在图片没读到的情况下编造描述。
- 不重复抓同一条内容；以脚本的 `duplicate` 判断为准。

---

## 7. 常见故障

| 现象 | 原因 | 处理 |
|------|------|------|
| `解析服务 30 秒内未就绪` | 首次 clone / 装依赖慢，或端口被占 | 看 `<DOUYIN_API_DIR>/server.log`；改 `DOUYIN_PORT` |
| `解析失败：...风控...` | 未登录 / 链接失效 | 导出 `cookies.txt`，设 `DOUYIN_COOKIE_FILE` |
| `所有图片都下载失败` | 图片 CDN 被限 / 网络断 | 重试；配置 cookie；检查网络 |
| `未找到 Obsidian 库根目录` | 没传 `--vault`，自动探测失败 | 显式传 `--vault "<库根>"` |
| `端口 ... 被其他程序占用` | 端口冲突 | 设 `DOUYIN_PORT` 换端口 |
| 中文乱码 | 终端编码不是 UTF-8 | 脚本已强制 UTF-8；仍乱码时用 `chcp 65001` |

---

## 8. 文件说明

| 文件 | 作用 |
|------|------|
| `scripts/dy_grab.py` | 唯一入口，跑一次抓一条图文 |
| `scripts/server_manager.py` | 解析服务启动 / 健康检查 / 端口复用 |
| `scripts/config.py` | 配置（全部可用环境变量覆盖） |
| `requirements.txt` | Python 依赖 |
| `docs/抓取SOP.md` | 更详细的执行流程（与本文件一致） |
| `tests/test_extract.py` | 离线单测：分享口令提链 |

解析库是第三方社区项目 [`Evil0ctal/Douyin_TikTok_Download_API`](https://github.com/Evil0ctal/Douyin_TikTok_Download_API)，本地部署，不裸调抖音接口。
