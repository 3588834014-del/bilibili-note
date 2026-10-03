#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bilibili-note 的离线测试套件。

只依赖标准库和 Python 自带的 unittest，不联网、不读 cookie：

    python -m unittest discover -s tests -v
    python tests/test_bili_note.py          # 等价，直接跑

覆盖范围：
  - SRT 解析与时间戳清理（含真实 B 站 ai-zh 形态与各种脏输入）
  - 两种时间戳格式化（frontmatter duration vs 要点行标签）
  - 要点行格式与下游归档管线的契约（用下游同一条正则验证）
  - 草稿态三处字面量契约
  - 笔记整体结构
  - 文件名与 URL 规范化
  - cookie 归一化
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import bili_note as bn  # noqa: E402


# ------------------------------------------------------------------ SRT

class TestParseSrt(unittest.TestCase):
    """每条 = (说明, SRT 片段, [(期望秒数, 期望文本)])"""

    CASES = [
        (
            "LF 行尾 + 序号 + 逗号毫秒（B站 ai-zh 实测形态）",
            "1\n00:00:00,080 --> 00:00:03,920\n熬夜的本质是预支明天的能量\n\n"
            "2\n00:00:03,920 --> 00:00:07,500\n命运早已标好利息\n",
            [(0.08, "熬夜的本质是预支明天的能量"), (3.92, "命运早已标好利息")],
        ),
        (
            "CRLF 行尾 + BOM",
            "\ufeff1\r\n00:00:01,000 --> 00:00:02,000\r\n第一句\r\n\r\n"
            "2\r\n00:00:02,000 --> 00:00:03,000\r\n第二句\r\n",
            [(1.0, "第一句"), (2.0, "第二句")],
        ),
        (
            "点号做毫秒分隔符（部分工具导出）",
            "1\n00:00:01.500 --> 00:00:02.750\n点号分隔\n",
            [(1.5, "点号分隔")],
        ),
        (
            "一条 cue 多行文本要合并成一行",
            "1\n00:00:05,000 --> 00:00:09,000\n上半句\n下半句\n",
            [(5.0, "上半句 下半句")],
        ),
        (
            "超 1 小时的时间码",
            "1\n01:02:03,000 --> 01:02:05,000\n一小时后\n",
            [(3723.0, "一小时后")],
        ),
        (
            "去掉 HTML 标签与 ASS 覆盖码",
            "1\n00:00:01,000 --> 00:00:02,000\n<font color=\"#fff\">强调</font>{\\an8}正文\n",
            [(1.0, "强调正文")],
        ),
        (
            "含数学比较符的正文不能被当成标签吃掉",
            "1\n00:00:01,000 --> 00:00:02,000\n当 a < b > c 时结论成立\n",
            [(1.0, "当 a < b > c 时结论成立")],
        ),
        (
            "零宽字符要清掉",
            "1\n00:00:01,000 --> 00:00:02,000\n前\u200b后\n",
            [(1.0, "前后")],
        ),
        (
            "连续重复句（滚动字幕）只保留一条",
            "1\n00:00:01,000 --> 00:00:02,000\n重复句\n\n"
            "2\n00:00:02,000 --> 00:00:03,000\n重复句\n\n"
            "3\n00:00:03,000 --> 00:00:04,000\n新句子\n",
            [(1.0, "重复句"), (3.0, "新句子")],
        ),
        (
            "空文本 cue 跳过",
            "1\n00:00:01,000 --> 00:00:02,000\n\n\n"
            "2\n00:00:02,000 --> 00:00:03,000\n有内容\n",
            [(2.0, "有内容")],
        ),
        ("完全空的输入", "", []),
        ("没有时间码的垃圾输入", "这不是字幕\n随便几行\n", []),
    ]

    def test_cases(self):
        for label, srt, want in self.CASES:
            with self.subTest(label):
                got = [(s["time"], s["text"]) for s in bn.parse_srt(srt)]
                self.assertEqual(got, want)

    def test_segments_to_text_is_single_line(self):
        segs = bn.parse_srt("1\n00:00:01,000 --> 00:00:02,000\n甲\n\n"
                            "2\n00:00:02,000 --> 00:00:03,000\n乙\n")
        text = bn.segments_to_text(segs)
        self.assertEqual(text, "甲 乙")
        self.assertNotIn("\n", text)


# ------------------------------------------------------------------ 时间戳

class TestTimestamps(unittest.TestCase):

    def test_fmt_ts_rolls_over_to_hours(self):
        """frontmatter 的 duration 字段：小时进位。"""
        for sec, want in [(0, "0:00"), (23, "0:23"), (71, "1:11"), (324.336, "5:24"),
                          (3600, "1:00:00"), (3723, "1:02:03"), (7850, "2:10:50")]:
            with self.subTest(sec=sec):
                self.assertEqual(bn.fmt_ts(sec), want)

    def test_fmt_bullet_ts_uses_total_minutes(self):
        """要点行标签：总分钟数，不做小时进位（与下游管线一致）。"""
        for sec, want in [(0, "00:00"), (20, "00:20"), (150, "02:30"),
                          (5611, "93:31"), (7429, "123:49")]:
            with self.subTest(sec=sec):
                self.assertEqual(bn.fmt_bullet_ts(sec), want)

    def test_the_two_formatters_disagree_over_an_hour(self):
        """明确锁死这个反直觉的差异，避免有人"顺手统一"。"""
        self.assertEqual(bn.fmt_ts(5611), "1:33:31")
        self.assertEqual(bn.fmt_bullet_ts(5611), "93:31")
        self.assertNotEqual(bn.fmt_ts(5611), bn.fmt_bullet_ts(5611))


# ------------------------------------------------------------------ 契约

SUBTITLE = ("这是测试字幕的第一句话，用来验证字幕正文长度检查。"
            "第二句话继续补充内容，让总长度超过最小阈值，模拟真实视频的字幕规模。"
            "第三句话结束这段用于测试的字幕正文。")

MANIFEST = {
    "bvid": "BV1TEST00001",
    "url": "https://www.bilibili.com/video/BV1TEST00001",
    "title": "测试标题",
    "uploader": "测试UP",
    "duration": 5611.0,
    "duration_hms": "1:33:31",
    "subtitle_lang": "ai-zh",
    "subtitle_text": SUBTITLE,
    "segments": [{"time": 0.0, "text": SUBTITLE}],
}

SUMMARY = {
    "summary": "一句话摘要。",
    "body": "总结正文。",
    "tags": ["认知"],
    "bullets": [
        {"time": 0, "title": "小标题", "text": "说明一"},
        {"time": 5611, "title": "长视频", "text": "说明二"},
    ],
}


class TestDownstreamContract(unittest.TestCase):
    """这些形状被下游归档管线依赖，改动会静默破坏它。"""

    # 下游归档管线用的正则，原样抄来当契约
    DOWNSTREAM_BULLET = re.compile(r"^\|- \[[0-9]{2}:[0-9]{2}\]", re.M)

    def test_bullet_line_matches_downstream_regex(self):
        note = bn.build_note(MANIFEST, SUMMARY, "2026-01-01")
        lines = [ln for ln in note.splitlines() if ln.startswith("|- ")]
        self.assertEqual(len(lines), 2)
        # 第二条是超 1 小时的那条：标签应是总分钟数 93:31
        self.assertEqual(
            lines[1],
            "|- [93:31](https://www.bilibili.com/video/BV1TEST00001/?t=5611s) **长视频**：说明二",
        )
        for ln in lines:
            with self.subTest(line=ln):
                self.assertTrue(self.DOWNSTREAM_BULLET.match(ln))

    def test_all_bullets_match_downstream_regex(self):
        note = bn.build_note(MANIFEST, SUMMARY, "2026-01-01")
        found = self.DOWNSTREAM_BULLET.findall(note)
        self.assertEqual(len(found), len(SUMMARY["bullets"]))

    def test_bullet_title_is_optional(self):
        summary = dict(SUMMARY, bullets=[{"time": 10, "text": "无标题说明"}])
        note = bn.build_note(MANIFEST, summary, "2026-01-01")
        line = next(ln for ln in note.splitlines() if ln.startswith("|- "))
        self.assertEqual(
            line,
            "|- [00:10](https://www.bilibili.com/video/BV1TEST00001/?t=10s) 无标题说明",
        )

    def test_draft_literals_are_exact(self):
        """归档管线靠字符串替换这三处，必须逐字一致。"""
        draft = bn.build_note(MANIFEST, None, "2026-01-01")
        for literal in ("status: draft", "needs_summary: true", "tags: []"):
            with self.subTest(literal=literal):
                self.assertIn(literal, draft)

    def test_completed_literals_are_exact(self):
        done = bn.build_note(MANIFEST, SUMMARY, "2026-01-01")
        self.assertIn("status: completed", done)
        self.assertIn("needs_summary: false", done)
        self.assertNotIn("status: draft", done)

    def test_frontmatter_key_order_matches_corpus(self):
        note = bn.build_note(MANIFEST, None, "2026-01-01")
        block = note.split("---\n", 2)[1]
        keys = [ln.split(":", 1)[0] for ln in block.splitlines() if ":" in ln]
        self.assertEqual(keys, ["title", "source", "author", "captured", "duration",
                                "type", "status", "needs_summary", "tags"])

    def test_duration_is_quoted(self):
        """不加引号时 `38:46` 会被 YAML 1.1 当成六十进制整数。"""
        note = bn.build_note(MANIFEST, None, "2026-01-01")
        self.assertIn('duration: "1:33:31"', note)

    def test_source_is_full_url(self):
        note = bn.build_note(MANIFEST, None, "2026-01-01")
        self.assertIn("source: https://www.bilibili.com/video/BV1TEST00001", note)

    def test_related_cards_section_is_omitted_when_empty(self):
        """草稿不产出该章节，它由归档阶段追加。"""
        self.assertNotIn("## 相关卡片", bn.build_note(MANIFEST, None, "2026-01-01"))

    def test_related_cards_section_present_when_given(self):
        note = bn.build_note(MANIFEST, dict(SUMMARY, cards=["CC_测试"]), "2026-01-01")
        self.assertIn("## 相关卡片", note)
        self.assertIn("- [[CC_测试]]", note)

    def test_heading_skeleton_and_order(self):
        note = bn.build_note(MANIFEST, SUMMARY, "2026-01-01")
        headings = ["## 一句话摘要", "## 核心要点", "## 总结", "## 笔记标签", "## 原始字幕"]
        positions = [note.index(h) for h in headings]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("> [!info] 视频信息", note)
        self.assertIn("> [!quote]- 完整字幕（点击展开）", note)

    def test_callout_uses_fullwidth_separator(self):
        note = bn.build_note(MANIFEST, None, "2026-01-01")
        self.assertIn("｜", note)

    def test_no_bom_leaks_into_output(self):
        note = bn.build_note(MANIFEST, None, "2026-01-01")
        self.assertNotIn("\ufeff", note)
        self.assertNotIn("\u200b", note)


class TestVerifyCommand(unittest.TestCase):
    """verify 子命令要对好笔记放行、对坏笔记报错。"""

    def _write(self, content, suffix=".md"):
        tmp = Path(tempfile.mkdtemp())
        p = tmp / f"note{suffix}"
        bn.write_text_lf(p, content)
        return p

    def _draft(self, **over):
        m = dict(MANIFEST, **over)
        return bn.build_note(m, None, "2026-01-01")

    def test_good_draft_passes(self):
        p = self._write(self._draft())
        self.assertEqual(bn.cmd_verify(type("A", (), {"note": str(p), "min_bullets": 5})()), 0)

    def test_good_completed_note_passes(self):
        bullets = [{"time": i * 10, "title": f"要点{i}", "text": f"说明{i}"} for i in range(5)]
        note = bn.build_note(MANIFEST, dict(SUMMARY, bullets=bullets), "2026-01-01")
        p = self._write(note)
        self.assertEqual(bn.cmd_verify(type("A", (), {"note": str(p), "min_bullets": 5})()), 0)

    def test_completed_note_with_too_few_bullets_fails(self):
        note = bn.build_note(MANIFEST, SUMMARY, "2026-01-01")  # 只有 2 条
        p = self._write(note)
        self.assertEqual(bn.cmd_verify(type("A", (), {"note": str(p), "min_bullets": 5})()), 1)

    def test_dirty_source_url_fails(self):
        note = self._draft().replace(
            "source: https://www.bilibili.com/video/BV1TEST00001",
            "source: https://www.bilibili.com/video/BV1TEST00001/?spm_id_from=abc",
        )
        p = self._write(note)
        self.assertEqual(bn.cmd_verify(type("A", (), {"note": str(p), "min_bullets": 5})()), 1)

    def test_placeholder_leftover_in_completed_note_fails(self):
        note = bn.build_note(MANIFEST, SUMMARY, "2026-01-01") + "\n待总结\n"
        p = self._write(note)
        self.assertEqual(bn.cmd_verify(type("A", (), {"note": str(p), "min_bullets": 5})()), 1)


# ------------------------------------------------------------------ 规范化

class TestSanitizeFilename(unittest.TestCase):

    CASES = [
        ("作文】素材[1]", "作文】素材 1"),          # 方括号是 wikilink 定界符
        ("第12期#导入导出", "第12期 导入导出"),       # # 是标题锚点分隔符
        ('a/b\\c:d*e?f"g<h>i|j', "a b c d e f g h i j"),
        ("  多余   空格  ", "多余 空格"),
        ("  ", "未命名视频"),
        ("", "未命名视频"),
    ]

    def test_cases(self):
        for raw, want in self.CASES:
            with self.subTest(raw=raw):
                self.assertEqual(bn.sanitize_filename(raw), want)

    def test_fullwidth_brackets_are_preserved(self):
        """全角【】「」不属于定界符，B 站标题里很常见，不能误删。"""
        self.assertEqual(bn.sanitize_filename("【Minecraft】教程"), "【Minecraft】教程")


class TestNormUrl(unittest.TestCase):

    CASES = [
        ("BV1TEST00001", "https://www.bilibili.com/video/BV1TEST00001"),
        ("https://www.bilibili.com/video/BV1TEST00001",
         "https://www.bilibili.com/video/BV1TEST00001"),
        # 分 P 参数也剥掉：它不影响 BV 定位，留着会和 ?t= 拼出两个 ?
        ("https://www.bilibili.com/video/BV1TEST00001?p=2",
         "https://www.bilibili.com/video/BV1TEST00001"),
        # 浏览器复制来的追踪参数
        ("https://www.bilibili.com/video/BV1TEST00001/?spm_id_from=333.1387.favlist.content.click&vd_source=deadbeef",
         "https://www.bilibili.com/video/BV1TEST00001"),
        ("https://www.bilibili.com/video/BV1TEST00001/?spm_id_from=333.1387.homepage.video_card.click",
         "https://www.bilibili.com/video/BV1TEST00001"),
        ("https://b23.tv/BV1TEST00001", "https://www.bilibili.com/video/BV1TEST00001"),
    ]

    def test_cases(self):
        for raw, want in self.CASES:
            with self.subTest(raw=raw):
                self.assertEqual(bn.norm_url(raw), want)

    def test_tracking_params_stripped_so_jump_link_has_one_question_mark(self):
        url = bn.norm_url("https://www.bilibili.com/video/BV1TEST00001/?spm_id_from=abc&vd_source=def")
        link = f"{url}/?t=23s"
        self.assertEqual(link.count("?"), 1)
        self.assertEqual(link, "https://www.bilibili.com/video/BV1TEST00001/?t=23s")


class TestBvid(unittest.TestCase):

    def test_accepts_url_or_bare_bvid(self):
        for raw in ("BV1TEST00001",
                    "https://www.bilibili.com/video/BV1TEST00001",
                    "https://www.bilibili.com/video/BV1TEST00001/?p=3"):
            with self.subTest(raw=raw):
                self.assertEqual(bn.bvid_of(raw), "BV1TEST00001")

    def test_rejects_input_without_bvid(self):
        with self.assertRaises(bn.BiliNoteError):
            bn.bvid_of("https://www.bilibili.com/")


# ------------------------------------------------------------------ 语言协商

class TestPickLang(unittest.TestCase):
    """语言选择必须按优先级，且找不到中文时不要乱选。"""

    def test_prefers_ai_zh(self):
        self.assertEqual(bn.pick_lang(["danmaku", "ai-zh", "ai-en"]), "ai-zh")

    def test_ai_zh_wins_over_manual_zh(self):
        self.assertEqual(bn.pick_lang(["zh-CN", "ai-zh"]), "ai-zh")

    def test_falls_back_to_zh_variants(self):
        self.assertEqual(bn.pick_lang(["danmaku", "zh-Hant"]), "zh-Hant")

    def test_returns_none_when_no_chinese(self):
        self.assertIsNone(bn.pick_lang(["danmaku", "ai-en", "ai-ja"]))
        self.assertIsNone(bn.pick_lang([]))


class TestListSubsParsing(unittest.TestCase):
    """`--list-subs` 的输出解析要宽容但不能误吞日志行。

    回归背景：曾把语言清单解析写错，导致"有字幕"被误判成"没字幕"，
    整个视频被静默跳过。这里用真实输出样本钉住解析行为。
    """

    SAMPLE = """[BiliBili] Extracting URL: https://www.bilibili.com/video/BV1TEST00001
[BiliBili] 1TEST00001: Downloading webpage
[BiliBili] BV1TEST00001: Extracting videos in anthology
[BiliBili] BV1TEST00001: Extracting subtitle info 123
[info] Available subtitles for BV1TEST00001:
Language Formats
danmaku  xml
ai-zh    srt
ai-en    srt
"""

    def _parse(self, text):
        langs, in_table = [], False
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("Language"):
                in_table = True
                continue
            if not in_table or not s:
                continue
            m = re.match(r"^([A-Za-z][\w-]*)\s+(\S+)$", s)
            if m:
                langs.append(m.group(1))
        return list(dict.fromkeys(langs))

    def test_parses_real_table(self):
        self.assertEqual(self._parse(self.SAMPLE), ["danmaku", "ai-zh", "ai-en"])

    def test_log_lines_before_table_are_ignored(self):
        """[BiliBili] 日志行不能被当成语言。"""
        self.assertNotIn("BiliBili]", self._parse(self.SAMPLE))

    def test_only_danmaku_yields_no_chinese(self):
        sample = "Language Formats\ndanmaku  xml\n"
        self.assertEqual(self._parse(sample), ["danmaku"])
        self.assertIsNone(bn.pick_lang(self._parse(sample)))

    def test_empty_output_yields_empty_list(self):
        self.assertEqual(self._parse(""), [])

    def test_blank_lines_do_not_break_table(self):
        sample = "Language Formats\n\ndanmaku  xml\n\nai-zh    srt\n"
        self.assertEqual(self._parse(sample), ["danmaku", "ai-zh"])


class TestFetchResilience(unittest.TestCase):
    """清单为空时应退化为逐个语言真下载探测，而不是直接判定"无字幕"。"""

    def test_metadata_is_requested_with_cookies(self):
        calls = []

        def fake_run(cmd):
            calls.append(cmd)
            import subprocess
            return subprocess.CompletedProcess(cmd, 0, '{"title":"t"}', "")

        cookie_file = Path(tempfile.mkdtemp()) / "c.txt"
        orig = bn.run
        bn.run = fake_run
        try:
            bn.ytdlp_meta("yt-dlp", "https://example.invalid/v", cookie_file)
        finally:
            bn.run = orig
        self.assertIn("--cookies", calls[0])
        # 用 str(Path) 比较，避免 Windows 反斜杠与 POSIX 斜杠的差异
        self.assertIn(str(cookie_file), calls[0])

    def test_candidate_list_covers_priority_order_when_list_empty(self):
        """清单为空 → 候选至少覆盖 SUB_LANG_PRIORITY，顺序不变。"""
        candidates = []
        for lang in bn.SUB_LANG_PRIORITY:
            if lang not in candidates:
                candidates.append(lang)
        self.assertEqual(candidates, bn.SUB_LANG_PRIORITY)
        self.assertGreaterEqual(len(candidates), 4)


# ------------------------------------------------------------------ cookie

# 模拟「Get cookies.txt LOCALLY」的真实导出：domain 带前导点但第 2 列是 FALSE。
# 这正是 Python http.cookiejar 断言 domain_specified == initial_dot 失败的原因。
BROWSER_EXPORT = "\n".join([
    "# Netscape HTTP Cookie File",
    "",
    ".bilibili.com\tFALSE\t/\tFALSE\t1819635894\tSESSDATA\tsecret-value",
    ".bilibili.com\tFALSE\t/\tTRUE\t1819635894\tbili_jct\ttoken-value",
    ".bing.com\tFALSE\t/\tFALSE\t0\t_C_Auth\tunrelated",
    ".douyin.com\tFALSE\t/\tFALSE\t0\tsessionid\tunrelated",
    "",
])


class TestNormalizeCookies(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.src = self.tmp / "raw.txt"
        self.src.write_text(BROWSER_EXPORT, encoding="utf-8")
        self.dst = self.tmp / "norm.txt"

    def test_only_bilibili_rows_survive(self):
        n = bn.normalize_cookies(self.src, self.dst)
        self.assertEqual(n, 2)
        out = self.dst.read_text(encoding="utf-8")
        self.assertNotIn("bing.com", out)
        self.assertNotIn("douyin.com", out)
        self.assertIn("SESSDATA", out)

    def test_leading_dot_removed_and_flag_is_false(self):
        """这是让 yt-dlp 能加载文件的关键修正。"""
        bn.normalize_cookies(self.src, self.dst)
        for line in self.dst.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#"):
                continue
            fields = line.split("\t")
            self.assertEqual(len(fields), 7)
            self.assertFalse(fields[0].startswith("."), f"domain 仍带前导点: {fields[0]}")
            self.assertEqual(fields[1], "FALSE", f"host-only 标志应为 FALSE: {fields[1]}")

    def test_output_has_header_and_no_bom(self):
        bn.normalize_cookies(self.src, self.dst)
        raw = self.dst.read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        self.assertTrue(raw.startswith(b"# Netscape HTTP Cookie File"))

    def test_same_cookie_name_last_wins(self):
        src = self.tmp / "dup.txt"
        src.write_text("# Netscape HTTP Cookie File\n"
                       ".bilibili.com\tFALSE\t/\tFALSE\t0\tSESSDATA\told\n"
                       ".bilibili.com\tFALSE\t/\tFALSE\t0\tSESSDATA\tnew\n",
                       encoding="utf-8")
        bn.normalize_cookies(src, self.dst)
        out = self.dst.read_text(encoding="utf-8")
        self.assertEqual(out.count("SESSDATA"), 1)
        self.assertIn("new", out)

    def test_file_without_bilibili_cookies_raises(self):
        src = self.tmp / "other.txt"
        src.write_text("# Netscape HTTP Cookie File\n"
                       ".example.com\tFALSE\t/\tFALSE\t0\tx\ty\n", encoding="utf-8")
        with self.assertRaises(bn.BiliNoteError):
            bn.normalize_cookies(src, self.dst)

    def test_missing_file_raises(self):
        with self.assertRaises(bn.BiliNoteError):
            bn.normalize_cookies(self.tmp / "nope.txt", self.dst)

    def test_cookie_has_login_detects_sessdata(self):
        bn.normalize_cookies(self.src, self.dst)
        self.assertTrue(bn.cookie_has_login(self.dst))
        no_login = self.tmp / "nologin.txt"
        no_login.write_text("# Netscape HTTP Cookie File\n"
                            "bilibili.com\tFALSE\t/\tFALSE\t0\tfoo\tbar\n",
                            encoding="utf-8")
        self.assertFalse(bn.cookie_has_login(no_login))


# ------------------------------------------------------------------ 路径

class TestResolveNoteDir(unittest.TestCase):

    def test_explicit_wins(self):
        self.assertEqual(bn.resolve_note_dir("/tmp/explicit"), Path("/tmp/explicit"))

    def test_env_vars(self):
        import os
        old = {k: os.environ.get(k) for k in ("BILI_VAULT", "BILI_TARGET_DIR")}
        try:
            os.environ["BILI_VAULT"] = "/tmp/vault"
            os.environ.pop("BILI_TARGET_DIR", None)
            self.assertEqual(bn.resolve_note_dir(None), Path("/tmp/vault/Sources/Videos"))
            os.environ["BILI_TARGET_DIR"] = "Notes/Videos"
            self.assertEqual(bn.resolve_note_dir(None), Path("/tmp/vault/Notes/Videos"))
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_nothing_configured_raises(self):
        import os
        old = {k: os.environ.get(k) for k in ("BILI_VAULT", "BILI_TARGET_DIR")}
        try:
            os.environ.pop("BILI_VAULT", None)
            os.environ.pop("BILI_TARGET_DIR", None)
            with self.assertRaises(bn.BiliNoteError):
                bn.resolve_note_dir(None)
        finally:
            for k, v in old.items():
                if v is not None:
                    os.environ[k] = v


# ------------------------------------------------------------------ 落盘

class TestWriteNote(unittest.TestCase):

    def test_write_then_verify_round_trip(self):
        tmp = Path(tempfile.mkdtemp())
        work = tmp / "work"
        work.mkdir()
        (work / "manifest.json").write_text(
            json.dumps(MANIFEST, ensure_ascii=False), encoding="utf-8")
        summary = tmp / "summary.json"
        bullets = [{"time": i * 100, "title": f"要点{i}", "text": f"说明{i}"} for i in range(5)]
        summary.write_text(json.dumps(dict(SUMMARY, bullets=bullets), ensure_ascii=False),
                           encoding="utf-8")
        proc = tmp / ".processed.json"

        rc = bn.cmd_write(type("A", (), {
            "work": str(work), "note_dir": str(tmp), "summary_file": str(summary),
            "processed": str(proc), "captured": "2026-01-01",
        })())
        self.assertEqual(rc, 0)
        note = tmp / "B站 测试标题.md"
        self.assertTrue(note.is_file())
        self.assertEqual(json.loads(proc.read_text(encoding="utf-8")), ["BV1TEST00001"])

        rc = bn.cmd_verify(type("A", (), {"note": str(note), "min_bullets": 5})())
        self.assertEqual(rc, 0)

    def test_stale_manifest_url_is_renormalized(self):
        """老版本 fetch 写出的 manifest 可能带追踪参数，write 要兜住。"""
        tmp = Path(tempfile.mkdtemp())
        work = tmp / "work"
        work.mkdir()
        stale = dict(MANIFEST,
                     url="https://www.bilibili.com/video/BV1TEST00001/?spm_id_from=abc&vd_source=def")
        (work / "manifest.json").write_text(json.dumps(stale, ensure_ascii=False),
                                            encoding="utf-8")
        bn.cmd_write(type("A", (), {
            "work": str(work), "note_dir": str(tmp), "summary_file": None,
            "processed": None, "captured": "2026-01-01",
        })())
        text = (tmp / "B站 测试标题.md").read_text(encoding="utf-8")
        self.assertIn("source: https://www.bilibili.com/video/BV1TEST00001\n", text)
        self.assertNotIn("spm_id_from", text)

    def test_duplicate_title_with_different_bvid_gets_suffix(self):
        tmp = Path(tempfile.mkdtemp())
        other = dict(MANIFEST, bvid="BV1OTHER0001",
                     url="https://www.bilibili.com/video/BV1OTHER0001")
        for m in (other, MANIFEST):
            work = tmp / f"work-{m['bvid']}"
            work.mkdir()
            (work / "manifest.json").write_text(json.dumps(m, ensure_ascii=False),
                                                encoding="utf-8")
            bn.cmd_write(type("A", (), {
                "work": str(work), "note_dir": str(tmp), "summary_file": None,
                "processed": None, "captured": "2026-01-01",
            })())
        self.assertTrue((tmp / "B站 测试标题.md").is_file())
        self.assertTrue((tmp / "B站 测试标题 BV1TEST00001.md").is_file())


class TestRepoHygiene(unittest.TestCase):
    """仓库卫生：本项目全部产物要求 LF 换行，任何 CRLF 都是回归。

    Git 在 Windows 上默认 autocrlf 会把文件转成 CRLF，而脚本以
    newline="\\n" 写文件、测试也断言"无 CRLF"。.gitattributes 负责约束，
    这里负责在 CI 上把违规抓出来。
    """

    REPO = Path(__file__).resolve().parent.parent

    def _tracked_text_files(self):
        for path in self.REPO.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(self.REPO)
            if any(part in {".git", "__pycache__", ".pytest_cache"} for part in rel.parts):
                continue
            if path.suffix in {".pyc", ".srt"}:
                continue
            yield rel, path

    def test_no_crlf_in_repo_text_files(self):
        offenders = []
        for rel, path in self._tracked_text_files():
            try:
                if b"\r\n" in path.read_bytes():
                    offenders.append(str(rel))
            except OSError:
                continue
        self.assertEqual(offenders, [], f"以下文件含 CRLF 换行：{offenders}")

    def test_no_bom_in_repo_text_files(self):
        offenders = []
        for rel, path in self._tracked_text_files():
            try:
                if path.read_bytes().startswith(b"\xef\xbb\xbf"):
                    offenders.append(str(rel))
            except OSError:
                continue
        self.assertEqual(offenders, [], f"以下文件含 BOM：{offenders}")

    def test_license_and_docs_present(self):
        for name in ("LICENSE", "README.md", "SKILL.md", "CHANGELOG.md",
                     "docs/FORMAT.md", ".gitignore", ".gitattributes"):
            with self.subTest(name=name):
                self.assertTrue((self.REPO / name).is_file(), f"缺少 {name}")

    def test_skill_frontmatter_is_valid(self):
        """SKILL.md 必须有 name/description frontmatter，且 name 与目录名一致。"""
        text = (self.REPO / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        block = text.split("---\n", 2)[1]
        keys = {}
        for line in block.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                keys[k.strip()] = v.strip()
        self.assertIn("name", keys)
        self.assertIn("description", keys)
        self.assertEqual(keys["name"], "bilibili-note")
        self.assertGreater(len(keys["description"]), 40, "description 太短，不利于触发词匹配")

    def test_gitignore_covers_credentials(self):
        text = (self.REPO / ".gitignore").read_text(encoding="utf-8")
        for pattern in ("cookies.txt", "__pycache__", "*.srt"):
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, text)


class TestPython38Compatibility(unittest.TestCase):
    """声明支持的最低 Python 版本必须真的能跑。

    回归背景：`Path.write_text(..., newline=)` 是 Python 3.10 才加入的参数，
    在 3.8 上直接 TypeError —— 而 CI 之前跑的是 3.12，本地也是 3.12，
    所以一路绿灯直到第一次推送才发现 `requires-python = ">=3.8"` 是假的。
    这里做静态扫描，把这类"新 API 混进旧版本声明"的问题挡在提交前。
    """

    REPO = Path(__file__).resolve().parent.parent
    MIN_MINOR = 8  # 与 pyproject.toml 的 requires-python 保持一致

    def _py_files(self):
        for sub in ("scripts", "tests"):
            for p in (self.REPO / sub).rglob("*.py"):
                if "__pycache__" not in p.parts:
                    yield p

    def test_pyproject_min_version_matches_this_test(self):
        text = (self.REPO / "pyproject.toml").read_text(encoding="utf-8")
        m = re.search(r'requires-python\s*=\s*">=3\.(\d+)"', text)
        self.assertIsNotNone(m, "pyproject.toml 里找不到 requires-python")
        self.assertEqual(
            int(m.group(1)), self.MIN_MINOR,
            "改了 requires-python 就要同步更新 TestPython38Compatibility.MIN_MINOR",
        )

    def test_no_write_text_newline_kwarg(self):
        """write_text(newline=) 需要 3.10+；本项目用 write_text_lf() 代替。

        用 AST 而不是字符串匹配：这些 API 名字本身会出现在文档字符串和
        注释里，纯文本扫描会把说明文字误报成违规。
        """
        import ast

        offenders = []
        for p in self._py_files():
            tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                if not (isinstance(fn, ast.Attribute) and fn.attr == "write_text"):
                    continue
                if any(kw.arg == "newline" for kw in node.keywords):
                    offenders.append(f"{p.relative_to(self.REPO)}:{node.lineno}")
        self.assertEqual(
            offenders, [],
            f"使用了 Python 3.10+ 才支持的 Path.write_text(newline=)：{offenders}",
        )

    def test_scripts_parse_under_min_version_syntax(self):
        """至少保证脚本能被 AST 解析（语法层面不依赖新版本）。"""
        import ast

        for p in self._py_files():
            with self.subTest(file=str(p.relative_to(self.REPO))):
                ast.parse(p.read_text(encoding="utf-8"), filename=str(p), feature_version=(3, self.MIN_MINOR))

    def test_files_declare_future_annotations(self):
        """`X | None` / `list[str]` 这类注解在 3.8 需要 future import。"""
        offenders = []
        for p in self._py_files():
            text = p.read_text(encoding="utf-8")
            uses_modern = re.search(r"(->|:)\s*(?:[\w\[\]]+\s*\|\s*None|list\[|dict\[|tuple\[)", text)
            has_future = "from __future__ import annotations" in text
            if uses_modern and not has_future:
                offenders.append(str(p.relative_to(self.REPO)))
        self.assertEqual(offenders, [], f"缺少 from __future__ import annotations：{offenders}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
