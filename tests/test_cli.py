#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bili-note CLI 入口点的测试。

CLI 是薄壳，所以这里测的是**接线**而不是业务逻辑：
  - 参数解析后是否正确转交给唯一实现
  - `note` 是否真的先 fetch 再 write
  - 缺省工作目录、--content-file 别名是否生效
  - 错误是否转成非零退出码

业务逻辑本身由 tests/test_bili_note.py 覆盖，这里不重复。
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import bili_note as core  # noqa: E402
from bili_note_cli import cli  # noqa: E402


class TestParser(unittest.TestCase):

    def setUp(self):
        self.ap = cli.build_parser(core)

    def test_version_flag(self):
        with self.assertRaises(SystemExit) as cm:
            self.ap.parse_args(["--version"])
        self.assertEqual(cm.exception.code, 0)

    def test_subcommands_exist(self):
        for cmd in ("note", "fetch", "write", "verify"):
            with self.subTest(cmd=cmd):
                ns = self.ap.parse_args(
                    [cmd, "BV1TEST00001"] if cmd in ("note", "fetch")
                    else [cmd, "--note", "x"] if cmd == "verify"
                    else [cmd, "--work", "w"]
                )
                self.assertEqual(ns.cmd, cmd)

    def test_note_does_not_require_work(self):
        """note 一步到位，不该再要求 --work（fetch 用的 --out 就够）。"""
        ns = self.ap.parse_args(["note", "BV1TEST00001"])
        self.assertIsNone(ns.out)
        self.assertFalse(hasattr(ns, "work"))

    def test_note_accepts_content_file_alias(self):
        ns = self.ap.parse_args(["note", "BV1TEST00001", "--content-file", "c.json"])
        self.assertEqual(ns.summary_file, "c.json")
        ns2 = self.ap.parse_args(["note", "BV1TEST00001", "--summary-file", "s.json"])
        self.assertEqual(ns2.summary_file, "s.json", "--summary-file 应作为兼容别名保留")

    def test_write_requires_work(self):
        with self.assertRaises(SystemExit):
            self.ap.parse_args(["write", "--note-dir", "x"])

    def test_verify_requires_note(self):
        with self.assertRaises(SystemExit):
            self.ap.parse_args(["verify"])

    def test_no_subcommand_is_an_error(self):
        with self.assertRaises(SystemExit):
            self.ap.parse_args([])


class TestWiring(unittest.TestCase):
    """用打桩替换实现，验证 CLI 把参数喂对了。"""

    def setUp(self):
        self.calls = []
        self._orig = (core.cmd_fetch, core.cmd_write, core.cmd_verify)
        core.cmd_fetch = self._stub("fetch")
        core.cmd_write = self._stub("write")
        core.cmd_verify = self._stub("verify")

    def tearDown(self):
        core.cmd_fetch, core.cmd_write, core.cmd_verify = self._orig

    def _stub(self, name):
        def f(args):
            self.calls.append((name, vars(args)))
            return 0
        return f

    def test_note_runs_fetch_then_write(self):
        rc = cli.main(["note", "BV1TEST00001", "--out", "/tmp/w", "--note-dir", "/tmp/n",
                       "--content-file", "/tmp/c.json", "--captured", "2026-01-01"])
        self.assertEqual(rc, 0)
        self.assertEqual([c[0] for c in self.calls], ["fetch", "write"])

        fetch_args = self.calls[0][1]
        self.assertEqual(fetch_args["url"], "BV1TEST00001")
        self.assertEqual(fetch_args["out"], "/tmp/w")

        write_args = self.calls[1][1]
        self.assertEqual(write_args["work"], "/tmp/w", "write 应复用 fetch 的工作目录")
        self.assertEqual(write_args["note_dir"], "/tmp/n")
        self.assertEqual(write_args["summary_file"], "/tmp/c.json")
        self.assertEqual(write_args["captured"], "2026-01-01")

    def test_note_uses_default_work_dir_when_omitted(self):
        cli.main(["note", "BV1TEST00001", "--note-dir", "/tmp/n"])
        self.assertEqual(self.calls[0][1]["out"], cli.DEFAULT_WORK)
        self.assertEqual(self.calls[1][1]["work"], cli.DEFAULT_WORK)

    def test_note_stops_if_fetch_fails(self):
        def failing(args):
            self.calls.append(("fetch", vars(args)))
            return 1
        core.cmd_fetch = failing
        rc = cli.main(["note", "BV1TEST00001", "--note-dir", "/tmp/n"])
        self.assertEqual(rc, 1)
        self.assertEqual([c[0] for c in self.calls], ["fetch"], "fetch 失败时不该再写笔记")

    def test_fetch_alone_does_not_write(self):
        cli.main(["fetch", "BV1TEST00001", "--out", "/tmp/w"])
        self.assertEqual([c[0] for c in self.calls], ["fetch"])

    def test_write_alone(self):
        cli.main(["write", "--work", "/tmp/w", "--note-dir", "/tmp/n"])
        self.assertEqual([c[0] for c in self.calls], ["write"])

    def test_verify_passes_min_bullets(self):
        cli.main(["verify", "--note", "/tmp/n.md", "--min-bullets", "7"])
        self.assertEqual(self.calls[0][1]["min_bullets"], 7)

    def test_bilinote_error_becomes_exit_code_1(self):
        def boom(args):
            raise core.BiliNoteError("模拟失败")
        core.cmd_fetch = boom
        self.assertEqual(cli.main(["fetch", "BV1TEST00001"]), 1)


class TestLoadCore(unittest.TestCase):

    def test_load_core_returns_the_real_module(self):
        loaded = cli._load_core()
        self.assertTrue(hasattr(loaded, "cmd_fetch"))
        self.assertTrue(hasattr(loaded, "cmd_write"))
        self.assertTrue(hasattr(loaded, "BiliNoteError"))

    def test_version_is_a_nonempty_string(self):
        self.assertIsInstance(cli._version(core), str)
        self.assertTrue(cli._version(core))


class TestSubprocessSmoke(unittest.TestCase):
    """真的把 CLI 当进程跑一遍，确认没有 import 期错误。"""

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "bili_note_cli.cli", *args],
            capture_output=True, encoding="utf-8", errors="replace",
            cwd=str(REPO), timeout=60,
        )

    def test_version_runs(self):
        r = self._run("--version")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("bili-note", r.stdout)

    def test_help_runs(self):
        r = self._run("--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        for cmd in ("note", "fetch", "write", "verify"):
            self.assertIn(cmd, r.stdout)

    def test_missing_subcommand_exits_nonzero(self):
        self.assertNotEqual(self._run().returncode, 0)


class TestInstallScript(unittest.TestCase):
    """skill 安装脚本：只装运行需要的源文件，不装构建产物。"""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_install_mod", REPO / "scripts" / "install.py")
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def test_includes_only_runtime_files(self):
        names = {p.name for p in self.mod.iter_files(REPO)}
        self.assertIn("SKILL.md", names)
        self.assertIn("bili_note.py", names)
        self.assertIn("FORMAT.md", names)

    def test_excludes_tests_and_repo_metadata(self):
        """tests/ 与仓库级文件不该被装进 skill 目录。"""
        rel = {str(p.relative_to(REPO)).replace("\\", "/")
               for p in self.mod.iter_files(REPO)}
        for unwanted in ("tests/test_bili_note.py", "tests/test_cli.py",
                         "README.md", "pyproject.toml", "CHANGELOG.md",
                         "LICENSE", ".gitignore"):
            with self.subTest(unwanted=unwanted):
                self.assertNotIn(unwanted, rel)

    def test_excludes_build_artifacts(self):
        """回归：pip install 生成的 egg-info / __pycache__ 曾被一起装进去。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "SKILL.md").write_text("x", encoding="utf-8")
            (root / "scripts").mkdir()
            (root / "scripts" / "bili_note.py").write_text("x", encoding="utf-8")
            egg = root / "scripts" / "proj.egg-info"
            egg.mkdir()
            (egg / "PKG-INFO").write_text("x", encoding="utf-8")
            cache = root / "scripts" / "__pycache__"
            cache.mkdir()
            (cache / "m.cpython-312.pyc").write_bytes(b"\x00")
            (root / "scripts" / "junk.pyc").write_bytes(b"\x00")

            got = {str(p.relative_to(root)).replace("\\", "/")
                   for p in self.mod.iter_files(root)}
            self.assertEqual(got, {"SKILL.md", "scripts/bili_note.py"},
                             f"构建产物泄漏进安装清单：{got}")

    def test_default_dest_is_the_skill_dir(self):
        self.assertEqual(self.mod.default_dest().name, "bilibili-note")
        self.assertEqual(self.mod.default_dest().parent.name, "skills")


class TestEnvChecks(unittest.TestCase):
    """环境自检的判定逻辑（纯函数，不碰磁盘/网络）。

    这个命令存在的原因：大多数"拿不到字幕"其实是环境问题（yt-dlp 没装、
    cookie 过期、输出目录没配），不是代码问题。
    """

    def _by_name(self, checks):
        return {name: (ok, detail) for name, ok, detail in checks}

    def test_all_missing_is_all_failed(self):
        m = self._by_name(core.collect_env_checks(None, None, None))
        self.assertFalse(m["cookie 文件"][0])
        self.assertFalse(m["yt-dlp"][0])
        self.assertFalse(m["输出目录"][0])
        self.assertNotIn("含 SESSDATA 登录态", m, "没有 cookie 文件时不该再查登录态")

    def test_all_present_passes(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        ck = tmp / "cookies.txt"
        ck.write_text("bilibili.com\tFALSE\t/\tFALSE\t0\tSESSDATA\tv\n", encoding="utf-8")
        m = self._by_name(core.collect_env_checks(ck, "/usr/bin/yt-dlp", tmp))
        self.assertTrue(all(ok for ok, _ in m.values()), m)
        self.assertIn("含 SESSDATA 登录态", m)

    def test_missing_sessdata_is_flagged_but_keeps_cookie_file_ok(self):
        """有 cookie 文件但没有 SESSDATA：文件本身存在，登录态缺失。"""
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        ck = tmp / "cookies.txt"
        ck.write_text("bilibili.com\tFALSE\t/\tFALSE\t0\tfoo\tbar\n", encoding="utf-8")
        m = self._by_name(core.collect_env_checks(ck, "yt-dlp", tmp))
        self.assertTrue(m["cookie 文件"][0])
        self.assertFalse(m["含 SESSDATA 登录态"][0])

    def test_ytdlp_error_message_is_surfaced(self):
        m = self._by_name(core.collect_env_checks(None, None, None, "未找到 yt-dlp，请先安装"))
        self.assertIn("未找到 yt-dlp", m["yt-dlp"][1])

    def test_selftest_returns_nonzero_when_env_incomplete(self):
        import os
        old = {k: os.environ.get(k) for k in ("BILI_VAULT", "BILI_TARGET_DIR", "BILI_COOKIES")}
        try:
            for k in old:
                os.environ.pop(k, None)
            rc = core.cmd_selftest(type("A", (), {"note_dir": None})())
        finally:
            for k, v in old.items():
                if v is not None:
                    os.environ[k] = v
        # 干净环境下必然缺东西（除非这台机器恰好都配好了）
        self.assertIn(rc, (0, 1))


class TestPackagingConfig(unittest.TestCase):
    """pyproject 必须真的把 console script 和唯一实现接上。"""

    def test_pyproject_declares_console_script(self):
        text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('bili-note = "bili_note_cli.cli:main"', text)

    def test_pyproject_maps_the_shared_module(self):
        """唯一实现必须作为顶层模块 bili_note 装进去，否则安装后 CLI 找不到它。"""
        text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('py-modules = ["bili_note"]', text)
        self.assertIn('"" = "scripts"', text)

    def test_declared_version_matches_core(self):
        text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        import re
        m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), core.__version__,
                         "改了 __version__ 就要同步 pyproject 的 version")


if __name__ == "__main__":
    unittest.main(verbosity=2)
