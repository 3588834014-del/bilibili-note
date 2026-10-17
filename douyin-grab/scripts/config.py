# -*- coding: utf-8 -*-
"""抖音图文抓取工具 · 配置文件（所有可调项集中在此）。

所有路径都可以用环境变量覆盖，便于把 skill 安装到任意位置：
    DOUYIN_VAULT          Obsidian 库根目录
    DOUYIN_INBOX          笔记输出目录（默认 00_Inbox）
    DOUYIN_ATTACH         图片目录（默认 attachments/douyin）
    DOUYIN_PORT           本地解析服务端口（默认 8783）
    DOUYIN_API_DIR        解析库 clone 目录
    DOUYIN_COOKIE_FILE    抖音 cookies.txt 路径
    DOUYIN_STATE_DIR      去重状态目录（默认 <vault>/.douyin_grab）
"""

import os
from pathlib import Path


# ---------- 路径 ----------
def _find_vault_root() -> str:
    """自动向上查找含 .obsidian 的文件夹，作为 Obsidian 库根路径。

    这样脚本不管装在哪一层，都能自动定位到当前库，避免手动填错路径。
    也可用环境变量 DOUYIN_VAULT 或命令行 --vault 显式指定。
    """
    env = os.environ.get("DOUYIN_VAULT", "").strip()
    if env:
        return str(Path(env).expanduser().resolve())

    p = Path(__file__).resolve().parent
    for _ in range(8):
        if (p / ".obsidian").is_dir():
            return str(p)
        p = p.parent
    return ""


_auto = _find_vault_root()

# Obsidian 库根目录（环境变量优先，其次自动探测；探测不到时用 --vault 传入）
VAULT_ROOT = _auto

# 相对 vault 根
# 脚本只负责图片/元数据；笔记由 agent 按 SOP 写入 INBOX_DIR
INBOX_DIR = os.environ.get("DOUYIN_INBOX", "00_Inbox")
ATTACH_DIR = os.environ.get("DOUYIN_ATTACH", "attachments/douyin")


# ---------- 解析服务（Evil0ctal/Douyin_TikTok_Download_API 本地部署） ----------
SERVER_HOST = "127.0.0.1"
SERVER_PORT = int(os.environ.get("DOUYIN_PORT", "8783"))

# 解析库 clone 目录（默认放到库外、用户主目录下，避免把 GitHub 源码灌进 iCloud 同步的 Obsidian 库。
# 也可手动指到已 clone 好的目录。）
API_REPO_DIR = os.environ.get("DOUYIN_API_DIR") or str(
    Path.home() / "douyin_grab_api" / "Douyin_TikTok_Download_API"
)

# 解析库的"取单个作品"接口路径。若将来解析库版本改接口，改这里即可。
API_FETCH_PATH = "/api/douyin/web/fetch_one_video"
API_REPO_URL = "https://github.com/Evil0ctal/Douyin_TikTok_Download_API"

# 可选：首次启动自动给解析库安装依赖（True 省心，False 手动装）
AUTO_INSTALL_DEPS = True


# ---------- 可选：登录 cookie ----------
# 提升解析成功率；不填也能跑，但遇风控时易失败。
# 用浏览器扩展 "Get cookies.txt LOCALLY" 导出 douyin.com 的 cookies。
_default_cookie = Path(__file__).resolve().parent / "cookies.txt"
COOKIE_FILE = os.environ.get("DOUYIN_COOKIE_FILE") or (
    str(_default_cookie) if _default_cookie.exists() else ""
)


# ---------- 行为 ----------
IMAGE_TIMEOUT = 20                   # 单图下载超时（秒）
HEALTH_TIMEOUT = 30                  # 等待解析服务就绪的最长秒数
DOWNLOAD_TIMEOUT = 15


def cookie_header() -> str:
    """读取 cookie 文件，返回可直接放进请求头/解析服务的 Cookie 字符串；无则返回空。"""
    if not COOKIE_FILE:
        return ""
    cpath = Path(COOKIE_FILE)
    if not cpath.is_absolute():
        cpath = Path(__file__).resolve().parent / cpath
    if not cpath.exists():
        return ""
    try:
        lines = cpath.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    cookies = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # 浏览器扩展常把 HttpOnly cookie 写成 "#HttpOnly_.douyin.com" 前缀
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7 and ("douyin" in parts[0] or "iesdouyin" in parts[0]):
            cookies[parts[5]] = parts[6].strip()
    return "; ".join(f"{k}={v}" for k, v in cookies.items())
