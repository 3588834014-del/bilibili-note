#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抖音图文 → Obsidian 抓取脚本（唯一入口）。

用法：
    python dy_grab.py "<抖音分享口令>"

职责（只做确定性的"脏活"，不含任何 AI / 大模型调用，无 API 密钥）：
    1. 从分享口令提取链接
    2. 用本地解析服务取回图文元数据
    3. 下载图片到 attachments/douyin/<aweme_id>/
    4. 写 meta.json，并在 stdout 最后一行输出 JSON 摘要
    5. 维护去重记录 processed.json

退出码：
    0  成功 / 重复
    2  口令里没找到链接
    3  解析失败（风控 / 链接失效）
    4  网络错误
    5  非图文内容（视频 / 直播等）
"""

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

import config
import server_manager

# 强制 stdout/stderr 用 UTF-8，确保 Codex / Claudian 按 UTF-8 读取时中文与 JSON 不乱码
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


EXIT_OK = 0
EXIT_NO_URL = 2
EXIT_PARSE_FAIL = 3
EXIT_NETWORK = 4
EXIT_NOT_IMAGE = 5
EXIT_CONFIG = 6

# 匹配分享口令里第一个 http(s) 链接，遇到空白或中文标点即停
URL_RE = re.compile(r"https?://[^\s，。！？；、（）：]+")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Referer": "https://www.douyin.com/"}


def _log(msg: str) -> None:
    print(msg, flush=True)


# ---------- 第 1 步：输入提取 ----------
def extract_url(text: str):
    if not text:
        return None
    m = URL_RE.search(text)
    if not m:
        return None
    return m.group(0).rstrip("，。；、,;()?）)")


# ---------- 路径 / 去重 ----------
def _vault_path(*rel) -> Path:
    if not config.VAULT_ROOT:
        raise RuntimeError("config.py 里 VAULT_ROOT 为空，请填写 Obsidian 库根路径")
    return Path(config.VAULT_ROOT).joinpath(*rel)


def _processed_path() -> Path:
    """去重状态放在库内 .douyin_grab/ 下；没有库根时退回脚本目录。"""
    env = os.environ.get("DOUYIN_STATE_DIR", "").strip()
    if env:
        state_dir = Path(env).expanduser()
    elif config.VAULT_ROOT:
        state_dir = Path(config.VAULT_ROOT) / ".douyin_grab"
    else:
        return Path(__file__).resolve().parent / "processed.json"
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return Path(__file__).resolve().parent / "processed.json"
    return state_dir / "processed.json"


def load_processed() -> list:
    p = _processed_path()
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []
    return []


def save_processed(data: list) -> None:
    _processed_path().write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def find_duplicate(aweme_id: str):
    if not aweme_id:
        return None
    for rec in load_processed():
        if str(rec.get("aweme_id", "")) == str(aweme_id):
            return rec
    return None


# ---------- 第 3 步：解析 ----------
def _pick_img_url(item):
    """从单张图片元素里挑出一张高清 URL。容错多种库版本返回结构。"""
    if isinstance(item, str):
        return item or None
    if not isinstance(item, dict):
        return None
    for key in ("url_list", "display_image", "download_url", "origin_cover", "url"):
        val = item.get(key)
        if isinstance(val, list) and val:
            # url_list 最后一个通常是最高清
            last = [u for u in val if isinstance(u, str) and u][-1:]
            return last[0] if last else None
        if isinstance(val, dict):
            sub = val.get("url_list") or val.get("url")
            if isinstance(sub, list) and sub:
                last = [u for u in sub if isinstance(u, str) and u][-1:]
                return last[0] if last else None
            if isinstance(sub, str) and sub:
                return sub
    return None


def _normalize_aweme(data: dict) -> dict:
    """把解析服务返回的 data 展平，提取我们需要的字段。"""
    detail = data.get("aweme_detail") or data
    if not isinstance(detail, dict):
        detail = {}

    aweme_id = detail.get("aweme_id") or detail.get("id") or data.get("aweme_id") or ""
    title = detail.get("desc") or data.get("desc") or ""
    caption = detail.get("desc") or data.get("desc") or title or ""

    author = ""
    auth = detail.get("author") or {}
    if isinstance(auth, dict):
        author = (
            auth.get("nickname")
            or auth.get("name")
            or detail.get("author_name")
            or ""
        )
    else:
        author = str(auth or detail.get("author_name") or "")

    create_time = detail.get("create_time") or data.get("create_time") or 0

    images_raw = detail.get("images") or data.get("images") or []
    image_urls = []
    for it in images_raw:
        u = _pick_img_url(it)
        if u:
            image_urls.append(u)

    return {
        "aweme_id": str(aweme_id),
        "title": (title or "").strip(),
        "caption": (caption or "").strip(),
        "author": (author or "").strip(),
        "create_time": create_time,
        "image_urls": image_urls,
        "detail": detail,
    }


def _is_image_post(norm: dict) -> bool:
    """判断是否为图文；视频/直播返回 False。"""
    detail = norm.get("detail") or {}
    if norm["image_urls"]:
        return True
    if detail.get("image_post_info"):
        return True
    if detail.get("is_image_post") is True:
        return True
    aweme_type = detail.get("aweme_type") or detail.get("aweme_type_v2")
    if aweme_type in (2, 68):  # 2=图集，68=图片
        return True
    return False


def _api_get(base: str, path: str, params: dict, headers: dict) -> dict:
    """请求本地解析服务并解析 JSON。"""
    try:
        resp = requests.get(
            base + path, params=params, headers=headers,
            timeout=config.DOWNLOAD_TIMEOUT,
        )
    except (requests.ConnectionError, requests.Timeout) as exc:
        raise _NetworkError(str(exc))
    except requests.RequestException as exc:
        raise _ParseFail("请求解析服务异常：" + str(exc))
    try:
        return resp.json()
    except ValueError:
        raise _ParseFail(
            "解析服务返回的不是 JSON（可能被抖音风控拦截），请稍后重试或配置 cookie"
        )


def _extract_id(data) -> str:
    """从 get_aweme_id 的返回里取作品 id（返回是字符串或 dict）。"""
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        return str(data.get("aweme_id") or data.get("id") or "")
    return ""


def fetch_aweme(link: str) -> dict:
    """调用本地解析服务（两步：先解析 id，再取详情），返回 normalized 信息。"""
    base = server_manager.ensure_server()
    req_headers = dict(HEADERS)
    cookie = config.cookie_header()
    if cookie:
        req_headers["Cookie"] = cookie

    # 第 1 步：从分享链接解析出 aweme_id
    id_body = _api_get(base, "/api/douyin/web/get_aweme_id", {"url": link}, req_headers)
    aweme_id = _extract_id(id_body.get("data"))

    if not aweme_id:
        # 有些旧版本接口仍接受 url，回退再试一次
        raw = _api_get(base, config.API_FETCH_PATH, {"url": link}, req_headers)
        data = raw.get("data") or raw.get("aweme_detail")
        if data:
            return _normalize_aweme(data)
        raise _ParseFail("无法解析出作品 ID，分享链接可能已失效")

    # 第 2 步：用 aweme_id 取详情
    det = _api_get(
        base, "/api/douyin/web/fetch_one_video",
        {"aweme_id": aweme_id}, req_headers,
    )
    code = det.get("code")
    if code not in (0, 200, "0", "200"):
        detail = det.get("detail") or {}
        msg = ""
        if isinstance(detail, dict):
            msg = detail.get("message") or ""
        if not msg or msg == "An error occurred.":
            msg = ("该内容未能取到数据——可能是视频/直播（本管线只支持图文），"
                   "或触发了抖音风控（建议在 config.py 配置 COOKIE_FILE）")
        raise _ParseFail(str(msg))

    data = det.get("data") or det.get("aweme_detail") or {}
    if not data:
        raise _ParseFail("未取到作品数据，链接可能已失效")
    return _normalize_aweme(data)


# ---------- 自定义异常 ----------
class _NetworkError(Exception):
    pass


class _ParseFail(Exception):
    pass


# ---------- 第 4 步：下载 ----------
def _ext_from_content_type(ct: str) -> str:
    ct = (ct or "").lower()
    if "jpeg" in ct or "jpg" in ct:
        return "jpg"
    if "webp" in ct.lower():
        return "webp"
    if "png" in ct:
        return "png"
    return "jpg"


def _ext_from_url(url: str) -> str:
    tail = (url.split("?")[0].rsplit(".", 1)[-1] or "").lower()
    if tail in ("jpg", "jpeg", "webp", "png", "gif"):
        return "jpg" if tail == "jpeg" else tail
    return "jpg"


def download_images(aweme_id: str, urls: list) -> tuple:
    """下载图片到 attachments/douyin/<aweme_id>/，返回 (成功文件名list, 失败URLlist)。"""
    att_dir = _vault_path(config.ATTACH_DIR, aweme_id)
    att_dir.mkdir(parents=True, exist_ok=True)
    ok_files, fail_urls = [], []
    for idx, url in enumerate(urls, start=1):
        ext = "jpg"
        last_err = ""
        for attempt in (1, 2):  # 重试 1 次
            try:
                r = requests.get(url, headers=HEADERS, timeout=config.IMAGE_TIMEOUT)
                if r.status_code != 200:
                    last_err = f"HTTP {r.status_code}"
                    continue
                ext = _ext_from_content_type(r.headers.get("Content-Type", ""))
                fname = f"{idx:02d}.{ext}"
                (att_dir / fname).write_bytes(r.content)
                ok_files.append(fname)
                break
            except (requests.RequestException, OSError) as exc:
                last_err = str(exc)
        else:
            fail_urls.append(url)
            _log(f"    图{idx:02d} 下载失败：{last_err}（已跳过）")
    return ok_files, fail_urls


# ---------- 主流程 ----------
def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="dy_grab.py",
        description="抖音图文 → Obsidian：解析分享口令、下载图片、写 meta.json。",
    )
    parser.add_argument("share", nargs="?", help="抖音分享口令（含链接）")
    parser.add_argument(
        "--vault", help="Obsidian 库根目录；不传则读 DOUYIN_VAULT 或自动探测 .obsidian"
    )
    parser.add_argument(
        "--inbox", help="笔记输出目录名（仅记录到配置，脚本本身不写笔记）"
    )
    args = parser.parse_args(argv)

    if args.vault:
        config.VAULT_ROOT = str(Path(args.vault).expanduser().resolve())
    if args.inbox:
        config.INBOX_DIR = args.inbox

    if not config.VAULT_ROOT:
        _log(
            "未找到 Obsidian 库根目录。请用 --vault \"<库根>\" 指定，"
            "或设置环境变量 DOUYIN_VAULT。"
        )
        return EXIT_CONFIG

    if not args.share:
        _log("用法：python dy_grab.py --vault \"<库根>\" \"<抖音分享口令>\"")
        return EXIT_NO_URL
    share = args.share

    _log("[1/4] 正在解析链接...")
    url = extract_url(share)
    if not url:
        _log("分享口令里没找到链接，请重新复制完整口令")
        return EXIT_NO_URL

    _log("[2/4] 请求解析服务...")
    try:
        norm = fetch_aweme(url)
    except _NetworkError as exc:
        _log(f"网络错误：无法连接解析服务或抖音（{exc}）。请检查网络后重试。")
        return EXIT_NETWORK
    except _ParseFail as exc:
        _log(f"解析失败：{exc}")
        return EXIT_PARSE_FAIL
    except RuntimeError as exc:  # server_manager 的启动/克隆失败
        _log(f"解析服务启动失败：{exc}")
        return EXIT_NETWORK
    except Exception as exc:  # 兜底：不打印堆栈，只给一句人话
        _log(f"解析异常：{exc}")
        return EXIT_PARSE_FAIL

    if not _is_image_post(norm):
        _log("这条是视频/直播，本管线暂只支持图文")
        return EXIT_NOT_IMAGE

    aweme_id = norm["aweme_id"]
    if not aweme_id:
        raise SystemExit("未取到 aweme_id，无法继续")

    # 去重检查（用 aweme_id 唯一键）
    dup = find_duplicate(aweme_id)
    if dup:
        _log("该内容之前已抓取过，跳过下载。")
        print(json.dumps({
            "status": "duplicate",
            "aweme_id": aweme_id,
            "note": dup.get("title", ""),
        }, ensure_ascii=False), flush=True)
        return EXIT_OK

    _log(f"[3/4] 下载图片（共 {len(norm['image_urls'])} 张）...")
    image_files, failed = download_images(aweme_id, norm["image_urls"])
    if not image_files:
        _log("所有图片都下载失败，已中止。（可稍后重试或配置 cookie）")
        return EXIT_PARSE_FAIL

    _log("[4/4] 写入元数据...")
    meta = {
        "aweme_id": aweme_id,
        "title": norm["title"],
        "caption": norm["caption"],
        "author": norm["author"],
        "create_time": norm["create_time"],
        "image_files": image_files,
        "failed_images": failed,
        "source_url": url,
        "grab_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    att_dir = _vault_path(config.ATTACH_DIR, aweme_id)
    meta_path = att_dir / "meta.json"
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 第 6 步：更新去重记录
    recs = load_processed()
    recs.append({
        "aweme_id": aweme_id,
        "title": meta["title"],
        "grab_time": meta["grab_time"],
        "meta_path": str(meta_path),
    })
    save_processed(recs)

    _log("已保存：图片 " + str(att_dir) + " / 元数据 meta.json")
    print(json.dumps({
        "status": "ok",
        "aweme_id": aweme_id,
        "title": meta["title"],
        "images": len(image_files),
        "failed": len(failed),
        "meta_path": str(meta_path),
    }, ensure_ascii=False), flush=True)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
