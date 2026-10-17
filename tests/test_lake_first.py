"""Lake-first tests for book-ai-tools-data."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from book_ai_tools import config, lake
from book_ai_tools.ingest import run_live_ingest
from book_ai_tools.store import load_records, seed_lake_from_captures


SAMPLE_TOOLS = [
    {
        "name": "Example AI Chat",
        "description": "Chat assistant",
        "url": "https://example.com/ai-chat?utm=1",
        "source": "ProductHunt",
        "vendor": "ExampleCo",
        "category": "chatbot",
        "pricing": "free",
        "upvotes": "100",
        "tags": "chatbot",
    },
    {
        "name": "Code Helper",
        "description": "Coding agent",
        "url": "https://www.example.com/code-helper/",
        "source": "TheresAnAIForThat",
        "vendor": "DevCo",
        "category": "coding",
        "pricing": "freemium",
        "tags": "coding",
    },
]


class IdentityTests(unittest.TestCase):
    def test_canonical_url_stable_id(self):
        a = lake.canonical_url("https://www.Example.com/ai-chat/?utm=1")
        b = lake.canonical_url("https://example.com/ai-chat")
        self.assertEqual(a, b)
        self.assertEqual(lake.stable_tool_id(a), lake.stable_tool_id(b))

    def test_normalize_fields(self):
        rec = lake.normalize_tool_record(
            SAMPLE_TOOLS[0],
            provider="public_directories",
            observed_at="2026-08-07T12:00:00Z",
            source_page_url="https://www.producthunt.com/topics/artificial-intelligence",
        )
        self.assertEqual(rec["name"], "Example AI Chat")
        self.assertEqual(rec["vendor"], "ExampleCo")
        self.assertEqual(rec["category"], "chatbot")
        self.assertEqual(rec["pricing"], "free")
        self.assertEqual(rec["observed_at"], "2026-08-07T12:00:00Z")
        self.assertEqual(rec["event_time"], "2026-08-07T12:00:00Z")
        self.assertTrue(rec["canonical_url"].startswith("https://example.com/ai-chat"))


class OrderingTests(unittest.TestCase):
    def test_fail_closed_no_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)

            def boom(**_k):
                raise lake.LakeIngestError("simulated")

            with mock.patch(
                "book_ai_tools.ingest.fetch_directory_page_raw",
                return_value=(
                    b"<html>ph</html>",
                    "text/html",
                    SAMPLE_TOOLS,
                ),
            ):
                with mock.patch.object(
                    lake, "ingest_provider_payload", side_effect=boom
                ):
                    with self.assertRaises(lake.LakeIngestError):
                        run_live_ingest(
                            sources=["producthunt"],
                            output_dir=out,
                            data_lake_uri=str(out / "lake"),
                            write_history=False,
                            project_csv=True,
                        )
            self.assertFalse((out / "ai_tools.csv").exists())


@unittest.skipUnless(
    lake.shared_runtime_available(),
    "shared data_lake runtime not installed (pip install -e .[lake])",
)
class RealLakeTests(unittest.TestCase):
    def test_html_landing_replay_and_fields(self):
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            self.skipTest("pyarrow missing")

        with tempfile.TemporaryDirectory() as tmp:
            lake_uri = str(Path(tmp) / "lake")
            html = b"<html><body>AI tools directory</body></html>"
            page = "https://www.producthunt.com/topics/artificial-intelligence"
            results = seed_lake_from_captures(
                [
                    {
                        "provider": "public_directories",
                        "raw": html,
                        "tools": SAMPLE_TOOLS,
                        "content_type": "text/html",
                        "source_page_url": page,
                    }
                ],
                data_lake_uri=lake_uri,
            )
            landings = [r for r in results if r["raw_key"].endswith(".html")]
            self.assertTrue(landings)
            raw_key = landings[0]["raw_key"]
            self.assertIn("provider=public_directories", raw_key)
            self.assertEqual(
                lake.landing_object_bytes(raw_key, data_lake_uri=lake_uri), html
            )
            self.assertEqual(
                lake.landing_object_bytes(raw_key, data_lake_uri=lake_uri),
                lake.landing_object_bytes(raw_key, data_lake_uri=lake_uri),
            )
            rows = lake.read_bronze_rows(
                config.LAKE_DATASET_TOOLS, data_lake_uri=lake_uri
            )
            self.assertEqual(len(rows), 2)
            payloads = [lake.parse_payload_json(r) for r in rows]
            names = {p["name"] for p in payloads}
            self.assertEqual(names, {"Example AI Chat", "Code Helper"})
            for p in payloads:
                self.assertTrue(p.get("canonical_url"))
                self.assertTrue(p.get("observed_at") or p.get("event_time"))

            with mock.patch.object(config, "DATA_LAKE_URI", lake_uri):
                api_payload = load_records()
            self.assertEqual(api_payload["source_kind"], "bronze_parquet")
            self.assertGreaterEqual(len(api_payload["items"]), 2)


class PolicyIngestTests(unittest.TestCase):
    def test_brave_and_firecrawl_blocked(self):
        from book_ai_tools.ingest import fetch_brave_search, fetch_firecrawl_search

        with mock.patch.object(config, "FREE_ONLY", True):
            self.assertEqual(fetch_brave_search("ai tools"), [])
            self.assertEqual(fetch_firecrawl_search("ai tools"), [])


class SharedAdapterTests(unittest.TestCase):
    def test_contract(self):
        self.assertEqual(config.LAKE_SOURCE, "book-ai-tools-data")
        self.assertEqual(config.LAKE_DOMAIN, "ai-tools")
        self.assertEqual(config.LAKE_DATASET_TOOLS, "ai_tools")
        self.assertEqual(config.SCHEMA_VERSION, "ai_tools.v1")
        self.assertEqual(int(config.API_PORT), 8107)


if __name__ == "__main__":
    src = Path(__file__).resolve().parents[1] / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    unittest.main()
