"""Edge-case regressions: overflowing numbers, odd digits, invisible names."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from book_ai_tools import ingest, lake, quality

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import scrape_ai_tools  # noqa: E402


class EdgeCaseTests(unittest.TestCase):
    def test_huge_json_int_is_rejected_not_raised(self) -> None:
        huge = json.loads("1" + "0" * 400)
        self.assertIsNone(quality.finite_number(huge))
        self.assertIsNone(quality.non_negative_number(huge))

    def test_invisible_only_text_is_blank(self) -> None:
        self.assertEqual(scrape_ai_tools._clean_text("​ ⁠﻿­"), "")
        self.assertEqual(scrape_ai_tools._clean_text("Co­pilot​"), "Copilot")


@unittest.skipUnless(
    lake.shared_runtime_available(),
    "shared data_lake runtime not installed (pip install -e .[lake])",
)
class UpvoteFilterTests(unittest.TestCase):
    def test_superscript_digit_upvotes_do_not_crash_the_filter(self) -> None:
        tools = [
            {"name": "A", "url": "https://a.example/x", "source": "ProductHunt", "upvotes": "²"},
            {"name": "B", "url": "https://b.example/y", "source": "ProductHunt", "upvotes": 50},
        ]
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            ingest, "fetch_directory_page_raw", return_value=(b"<html>x</html>", "text/html", tools)
        ), contextlib.redirect_stdout(io.StringIO()):
            result = ingest.run_live_ingest(
                sources=["producthunt"],
                output_dir=Path(tmp),
                data_lake_uri=str(Path(tmp) / "lake"),
                min_upvotes=10,
            )
        self.assertEqual(result["tools"], 1)


if __name__ == "__main__":
    unittest.main()
