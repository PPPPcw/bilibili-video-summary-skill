import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


sys.dont_write_bytecode = True
SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "bilibili-video-summary"
    / "scripts"
    / "prepare_bilibili.py"
)
SPEC = importlib.util.spec_from_file_location("prepare_bilibili", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class PrepareBilibiliTests(unittest.TestCase):
    def test_normalize_bv_identifier(self):
        self.assertEqual(
            MODULE.normalize_source("BV1hi7w6ME4Q", None),
            "https://www.bilibili.com/video/BV1hi7w6ME4Q",
        )

    def test_part_is_added_without_losing_existing_query(self):
        actual = MODULE.normalize_source(
            "https://www.bilibili.com/video/BV1hi7w6ME4Q?spm_id_from=test",
            3,
        )
        self.assertIn("spm_id_from=test", actual)
        self.assertIn("p=3", actual)

    def test_source_identifier(self):
        self.assertEqual(
            MODULE.source_identifier("https://www.bilibili.com/video/BV1hi7w6ME4Q?p=2"),
            ("bvid", "BV1hi7w6ME4Q"),
        )
        self.assertEqual(
            MODULE.source_identifier("https://www.bilibili.com/video/av123456"),
            ("aid", "123456"),
        )

    def test_srt_time_format(self):
        self.assertEqual(MODULE.format_srt_time(3723.456), "01:02:03,456")

    def test_subtitle_json_becomes_timestamped_text(self):
        payload = {
            "body": [
                {"from": 1.2, "to": 2.8, "content": "第一句"},
                {"from": 3.0, "to": 4.5, "content": "第二句"},
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            srt = Path(directory) / "transcript.srt"
            text = Path(directory) / "transcript.txt"
            MODULE.subtitle_json_to_srt(payload, srt)
            MODULE.subtitle_to_timestamped_text(srt, text)
            self.assertEqual(
                text.read_text(encoding="utf-8"),
                "[00:00:01] 第一句\n[00:00:03] 第二句\n",
            )


if __name__ == "__main__":
    unittest.main()
