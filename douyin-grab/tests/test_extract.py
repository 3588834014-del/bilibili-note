import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import dy_grab  # noqa: E402


class ExtractUrlTests(unittest.TestCase):
    def test_extracts_plain_url(self):
        self.assertEqual(
            dy_grab.extract_url("https://v.douyin.com/abc123/"),
            "https://v.douyin.com/abc123/",
        )

    def test_extracts_url_from_share_text(self):
        text = "3.28 复制打开抖音，看看【作者的作品】 https://v.douyin.com/abc123/ 复制此链接"
        self.assertEqual(
            dy_grab.extract_url(text),
            "https://v.douyin.com/abc123/",
        )

    def test_stops_at_chinese_punctuation(self):
        text = "看这个 https://v.douyin.com/abc123/，很有意思"
        self.assertEqual(
            dy_grab.extract_url(text),
            "https://v.douyin.com/abc123/",
        )

    def test_returns_none_without_url(self):
        self.assertIsNone(dy_grab.extract_url("这条没有链接"))
        self.assertIsNone(dy_grab.extract_url(""))


if __name__ == "__main__":
    unittest.main()
