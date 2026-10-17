# douyin-grab · 抖音图文 → Obsidian

把一条**抖音图文**（图集 + 文案）从分享口令抓成 Obsidian 素材：脚本负责提链接、解析、下载图片、写 `meta.json`、去重；agent 负责逐张读图、客观描述、按模板写笔记。

> 只支持**图文**。视频、直播、纯文字不走这条管线。
>
> 脚本**不调用任何 AI / 大模型，不需要 API 密钥**。

---

## 安装

### 1. 依赖

- Python 3.10+
- Git（首次运行需要 clone 本地解析库）

```bash
python -m pip install -r requirements.txt
```

### 2. 作为 skill 安装

把整个 `douyin-grab/` 目录放到你的 skills 目录，例如：

```text
~/.codex/skills/douyin-grab/
~/.claude/skills/douyin-grab/
~/.agents/skills/douyin-grab/
```

目录里必须有 `SKILL.md`，agent 才能识别。

### 3. 确认库根

脚本默认从自身位置向上找含 `.obsidian` 的目录。找不到时，运行时显式传：

```bash
python scripts/dy_grab.py --vault "/path/to/YourVault" "<抖音分享口令>"
```

也可以设环境变量 `DOUYIN_VAULT`。

---

## 使用

### 在 agent 里

直接说：

> 抓这条：`<抖音分享口令>`

agent 会按 `SKILL.md` 执行：跑脚本 → 读 `meta.json` 和图片 → 写笔记到 `00_Inbox`。

### 命令行

```bash
python scripts/dy_grab.py --vault "/path/to/YourVault" "<抖音分享口令>"
```

成功时 stdout 最后一行是 JSON：

```json
{
  "status": "ok",
  "aweme_id": "7...",
  "title": "标题",
  "images": 9,
  "failed": 0,
  "meta_path": ".../attachments/douyin/7.../meta.json"
}
```

重复内容返回 `{"status": "duplicate", ...}`，不会重复下载。

---

## 可选：导出登录 cookie

不配置也能跑，但抖音风控时容易解析失败。配置后成功率明显更高：

1. 浏览器安装扩展 **Get cookies.txt LOCALLY**。
2. 打开并登录 [douyin.com](https://www.douyin.com/)。
3. 点扩展图标 → Export，导出 `cookies.txt`。
4. 把 `cookies.txt` 放到 `scripts/` 下，或设 `DOUYIN_COOKIE_FILE` 指向它。

> `cookies.txt` 是登录凭据，**不要提交到 Git、不要贴进对话**。`.gitignore` 已忽略 `cookies*.txt`。

---

## 配置

所有配置都可以用环境变量覆盖，见 `scripts/config.py`：

| 环境变量 | 默认值 | 作用 |
|----------|--------|------|
| `DOUYIN_VAULT` | 自动探测 | Obsidian 库根 |
| `DOUYIN_INBOX` | `00_Inbox` | 笔记输出目录名 |
| `DOUYIN_ATTACH` | `attachments/douyin` | 图片目录（相对库根） |
| `DOUYIN_PORT` | `8783` | 本地解析服务端口 |
| `DOUYIN_API_DIR` | `~/douyin_grab_api/Douyin_TikTok_Download_API` | 解析库 clone 目录 |
| `DOUYIN_COOKIE_FILE` | `scripts/cookies.txt`（存在时） | 抖音 cookie 文件 |
| `DOUYIN_STATE_DIR` | `<vault>/.douyin_grab` | 去重状态目录 |

---

## 退出码

| 退出码 | 含义 |
|--------|------|
| `0` | 成功 / 重复 |
| `2` | 分享口令里没找到链接 |
| `3` | 解析失败（链接失效 / 风控 / 非图文数据） |
| `4` | 网络错误 / 解析服务起不来 |
| `5` | 非图文（视频 / 直播） |
| `6` | 未找到 Obsidian 库根 |

---

## 目录结构

```text
douyin-grab/
├── SKILL.md                 # agent 执行规约
├── README.md
├── LICENSE
├── requirements.txt
├── .gitignore
├── docs/
│   └── 抓取SOP.md
├── scripts/
│   ├── dy_grab.py           # 唯一入口
│   ├── server_manager.py    # 解析服务生命周期
│   └── config.py            # 配置
└── tests/
    └── test_extract.py
```

---

## 解析服务

首次运行会自动 clone 并启动第三方社区项目 [`Evil0ctal/Douyin_TikTok_Download_API`](https://github.com/Evil0ctal/Douyin_TikTok_Download_API)。它只在本机 `127.0.0.1` 上跑，不裸调抖音接口。

- 首次 clone + 装依赖可能需要几分钟。
- 服务日志：`<DOUYIN_API_DIR>/server.log`。
- 端口冲突：改 `DOUYIN_PORT`。
- 接口变动：升级解析库，或看它的 README。

---

## 测试

```bash
python -m unittest discover -s tests -v
```

---

## 许可

MIT。解析库归其原作者所有，使用时请遵守其许可证与抖音平台条款。
