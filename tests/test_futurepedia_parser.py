"""Offline Futurepedia parsing against a saved HTML page (no network)."""

from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "pages" / "futurepedia_page.html"
sys.path.insert(0, str(ROOT / "scripts"))

try:
    import scrape_ai_tools as fp  # noqa: E402
except (ImportError, RuntimeError):  # bs4/httpx missing: collector extras not installed
    fp = None

SOURCE = "https://www.futurepedia.io/ai-tools"


@unittest.skipIf(
    fp is None, "collector deps missing (pip install -r requirements-collect.txt)"
)
class FuturepediaParserTests(unittest.TestCase):
    def setUp(self):
        self.rows = fp.parse_html(
            FIXTURE.read_text(encoding="utf-8"), SOURCE, "productivity", 2
        )

    def test_keeps_only_unique_canonical_cards(self):
        self.assertEqual(
            [r["tool_id"] for r in self.rows], ["alpha-writer", "beta-bot"]
        )
        self.assertEqual(
            [r["url"] for r in self.rows],
            [
                "https://www.futurepedia.io/tool/alpha-writer",
                "https://www.futurepedia.io/tool/beta-bot",
            ],
        )

    def test_full_card_fields(self):
        alpha = self.rows[0]
        self.assertEqual(alpha["name"], "Alpha Writer")
        self.assertEqual(alpha["description"], "Drafts blog posts with AI.")
        self.assertEqual(alpha["rating"], 4.5)
        self.assertEqual(alpha["rating_count"], 1234)
        self.assertEqual(alpha["bookmark_count"], 567)
        self.assertEqual(alpha["pricing"], "Freemium")
        self.assertEqual(alpha["categories"], "Productivity,Copywriting")
        self.assertEqual(alpha["website_url"], "https://alpha.example.com/start")
        self.assertEqual(alpha["image_url"], "https://cdn.futurepedia.io/alpha.png")
        self.assertEqual(alpha["source_category"], "productivity")
        self.assertEqual(
            alpha["source_url"], "https://www.futurepedia.io/ai-tools/productivity"
        )
        self.assertEqual(alpha["page_number"], 2)

    def test_sparse_card_rejects_bad_values(self):
        beta = self.rows[1]
        self.assertEqual(beta["rating"], "")  # 7/5 is out of range
        self.assertEqual(beta["rating_count"], 0)
        self.assertEqual(beta["website_url"], "")  # javascript: link dropped
        self.assertEqual(beta["pricing"], "Free Trial")
        self.assertEqual(beta["description"], "")

    def test_rejects_invalid_category_and_source(self):
        html = FIXTURE.read_text(encoding="utf-8")
        with self.assertRaises(ValueError):
            fp.parse_html(html, SOURCE, "Not A Slug")
        with self.assertRaises(ValueError):
            fp.parse_html(html, "http://www.futurepedia.io/ai-tools", "productivity")

    def test_snapshot_and_history_written_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            fp.write_snapshot(self.rows, "2026-08-01T00:00:00Z", out, "ai_tools")
            fp.append_history(self.rows, "2026-08-01T00:00:00Z", out, "ai_tools")
            fp.append_history(self.rows[:1], "2026-08-02T00:00:00Z", out, "ai_tools")
            fp.write_raw([{"html": "<p>x</p>"}], out, "ai_tools")
            self.assertEqual(
                sorted(p.name for p in out.iterdir()),
                ["ai_tools.csv", "ai_tools_history.csv", "ai_tools_raw.json"],
            )
            with (out / "ai_tools.csv").open(newline="", encoding="utf-8") as f:
                snapshot = list(csv.DictReader(f))
            self.assertEqual(list(snapshot[0]), fp.SNAPSHOT_FIELDS)
            with (out / "ai_tools_history.csv").open(newline="", encoding="utf-8") as f:
                history = list(csv.DictReader(f))
            self.assertEqual(
                [h["tool_id"] for h in history],
                ["alpha-writer", "beta-bot", "alpha-writer"],
            )


if __name__ == "__main__":
    unittest.main()
