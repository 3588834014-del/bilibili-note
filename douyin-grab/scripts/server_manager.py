# -*- coding: utf-8 -*-
"""解析服务生命周期管理：启动 / 健康检查 / 端口复用。

解析库：Evil0ctal/Douyin_TikTok_Download_API（本地 FastAPI 服务）。
本模块只负责把这个服务拉起来、确认可用并返回基地址；服务常驻，下次复用。
"""

import sys
import time
import socket
import re
import subprocess
import urllib.request
from pathlib import Path

import config


BASE_URL = f"http://{config.SERVER_HOST}:{config.SERVER_PORT}"


def _log(msg) -> None:
    print(f"[server_manager] {msg}", flush=True)


def _no_window() -> int:
    """Windows 下窗口隐藏；非 Windows 返回 0。"""
    if sys.platform == "win32":
        return subprocess.CREATE_NO_WINDOW
    return 0


def _repo_dir() -> Path:
    return Path(config.API_REPO_DIR)


def server_alive() -> bool:
    """探测解析服务是否存活并返回 200。"""
    try:
        with urllib.request.urlopen(BASE_URL + "/", timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def _port_in_use() -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            return s.connect_ex((config.SERVER_HOST, config.SERVER_PORT)) == 0
    except OSError:
        return False


def _clone_repo() -> None:
    repo = _repo_dir()
    # 判断"已存在"：识别新旧版本常见的入口/标识之一即可，避免重复 clone
    has_entry = any(
        (repo / p).exists()
        for p in ("app/main.py", "main.py", "app.py", "README.md", ".git")
    )
    if repo.is_dir() and has_entry:
        _log("解析库已存在：" + str(repo))
        return
    _log("未找到解析库，开始 git clone（首次较慢，请稍候）...")
    try:
        r = subprocess.run(
            ["git", "clone", "--depth", "1", config.API_REPO_URL, str(repo)],
            capture_output=True, text=True, creationflags=_no_window(),
        )
    except FileNotFoundError:
        raise RuntimeError("本机没有 git，无法 clone 解析库。请先安装 Git 后重试。")
    if r.returncode != 0:
        raise RuntimeError(
            "git clone 解析库失败：" + (r.stderr or "请检查网络连接").strip()
        )
    _log("解析库 clone 成功")


def _install_deps(repo: Path) -> None:
    if not config.AUTO_INSTALL_DEPS:
        _log("已跳过自动安装依赖（config.AUTO_INSTALL_DEPS=False）")
        return
    req = repo / "requirements.txt"
    if not req.exists():
        _log("解析库没有 requirements.txt，跳过依赖安装")
        return
    _log("为解析库安装依赖（首次较慢，请稍候）...")
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(req)],
            capture_output=True, text=True, creationflags=_no_window(),
        )
    except Exception as exc:
        raise RuntimeError("pip 安装解析库依赖失败：" + str(exc))
    if r.returncode != 0:
        raise RuntimeError(
            "pip 安装解析库依赖失败，请手动安装后重试。"
            "（可先运行：pip install -r \"<解析库目录>/requirements.txt\"）"
        )
    _log("依赖安装完成")


def _inject_cookie(repo: Path) -> None:
    """把用户配置的抖音 cookie 写入解析库的 config.yaml（TokenManager→headers→Cookie）。

    库从 crawlers/douyin/web/config.yaml 读取 cookie；若不注入，抓取易被风控。
    """
    cookie = config.cookie_header()
    if not cookie:
        return
    cfgfile = repo / "crawlers" / "douyin" / "web" / "config.yaml"
    if not cfgfile.exists():
        _log("未找到解析库 cookie 配置文件，跳过注入")
        return
    try:
        lines = cfgfile.read_text(encoding="utf-8").splitlines()
    except OSError:
        _log("读取解析库配置文件失败，跳过 cookie 注入")
        return
    out = []
    replaced = False
    for line in lines:
        m = re.match(r"^(\s*)Cookie\s*:", line)
        if m:
            out.append(f"{m.group(1)}Cookie: {cookie}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        _log("解析库配置里没有 Cookie 行，跳过注入")
        return
    try:
        cfgfile.write_text("\n".join(out), encoding="utf-8")
        _log("已把抖音 cookie 注入解析库配置")
    except OSError as exc:
        _log("写入解析库 cookie 配置失败：" + str(exc))


def _build_start_cmd(repo: Path) -> list:
    """根据解析库实际入口选择启动命令，并注入主机/端口。"""
    # 新版本入口在 app/main.py（FastAPI app 变量为 app），用 uvicorn 启动
    if (repo / "app" / "main.py").exists():
        return [
            sys.executable, "-m", "uvicorn", "app.main:app",
            "--host", config.SERVER_HOST,
            "--port", str(config.SERVER_PORT),
        ]
    if (repo / "main.py").exists():
        # 旧版本官方推荐入口：uvicorn main:app
        return [
            sys.executable, "-m", "uvicorn", "main:app",
            "--host", config.SERVER_HOST,
            "--port", str(config.SERVER_PORT),
        ]
    if (repo / "app.py").exists():
        return [sys.executable, "app.py", "--port", str(config.SERVER_PORT)]
    # 兜底：交给解析库自带的入口，但仍尝试注入端口
    return [sys.executable, "app.py", "--port", str(config.SERVER_PORT)]


def _start_server(repo: Path) -> None:
    _install_deps(repo)
    cmd = _build_start_cmd(repo)
    _log("启动解析服务：" + " ".join(cmd))
    logf = repo / "server.log"
    with open(logf, "w", encoding="utf-8") as lf:
        subprocess.Popen(
            cmd, cwd=str(repo), stdout=lf, stderr=subprocess.STDOUT,
            creationflags=_no_window(),
        )


def ensure_server() -> str:
    """确保解析服务可用，返回基地址（如 http://127.0.0.1:8783）。

    已存活 → 直接复用；未存活 → clone（如需）→ 装依赖 → 拉起 → 等待健康检查。
    """
    if server_alive():
        _log("解析服务已在运行，复用")
        return BASE_URL

    if _port_in_use():
        raise RuntimeError(
            f"端口 {config.SERVER_PORT} 被其他程序占用且不是解析服务，"
            "请修改 config.py 里的 SERVER_PORT 换一个端口。"
        )

    _clone_repo()
    repo = _repo_dir()
    _inject_cookie(repo)
    _start_server(repo)

    deadline = time.time() + config.HEALTH_TIMEOUT
    while time.time() < deadline:
        if server_alive():
            _log("解析服务健康检查通过")
            return BASE_URL
        time.sleep(1.5)

    raise RuntimeError(
        f"解析服务 {config.HEALTH_TIMEOUT} 秒内未就绪。"
        f"请查看解析库目录下的 server.log（{repo / 'server.log'}）排查；"
        "也可能是网络/依赖/端口问题。"
    )
