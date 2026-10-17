"""Offline tests for book-ai-tools-data free-only API + lake-first."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from book_ai_tools import config, lake, policy
from book_ai_tools.api import DataProductHandler
from book_ai_tools.store import (
    load_csv_projection,
    load_history,
    load_records,
    seed_fixtures,
    seed_lake_from_captures,
)


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures"

SAMPLE_TOOLS = [
    {
        "name": "Example AI Chat",
        "description": "Chat assistant",
        "url": "https://example.com/ai-chat",
        "source": "fixture",
        "vendor": "ExampleCo",
        "upvotes": "100",
        "category": "chatbot",
        "pricing": "free",
        "tags": "chatbot",
    },
    {
        "name": "Code Helper",
        "description": "Coding agent",
        "url": "https://example.com/code-helper",
        "source": "fixture",
        "vendor": "DevCo",
        "upvotes": "50",
        "category": "coding",
        "pricing": "freemium",
        "tags": "coding",
    },
]


def _set_data_dir(path: Path):
    return mock.patch.object(config, "DATA_DIR", path)


def _set_lake_uri(uri: str):
    return mock.patch.object(config, "DATA_LAKE_URI", uri)


def _set_read_mode(mode: str, fallback: str = "error"):
    return mock.patch.multiple(
        config,
        LAKE_READ_MODE=mode,
        LAKE_READ_FALLBACK=fallback,
    )


def _lake_deps_available() -> bool:
    try:
        import duckdb  # noqa: F401
        import pyarrow  # noqa: F401
    except ImportError:
        return False
    return lake.shared_runtime_available()


@unittest.skipUnless(
    _lake_deps_available(),
    "pyarrow/duckdb + shared data_lake runtime required (pip install -e .[lake])",
)
class StoreLakeTests(unittest.TestCase):
    def test_bronze_not_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            lake_uri = str(Path(tmp) / "lake")
            seed_lake_from_captures(
                [
                    {
                        "provider": "public_directories",
                        "raw": b"<html>producthunt ai tools</html>",
                        "tools": SAMPLE_TOOLS,
                        "content_type": "text/html",
                        "source_page_url": "https://www.producthunt.com/topics/artificial-intelligence",
                    }
                ],
                data_lake_uri=lake_uri,
            )
            with _set_lake_uri(lake_uri):
                payload = load_records()
            self.assertEqual(payload["source_kind"], "bronze_parquet")
            self.assertNotIn("ai_tools.csv", payload["path"])
            self.assertGreaterEqual(len(payload["items"]), 2)
            item = payload["items"][0]
            self.assertTrue(item.get("name"))
            self.assertTrue(item.get("canonical_url") or item.get("url"))
            self.assertTrue(item.get("raw_object_key", "").startswith("landing/"))

    def test_csv_does_not_feed_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            lake_uri = str(root / "empty")
            Path(lake_uri).mkdir()
            with mock.patch.object(config, "FIXTURES_DIR", FIXTURE_ROOT):
                seed_fixtures(data_dir)
            self.assertGreaterEqual(
                len(load_csv_projection(kind="records", data_dir=data_dir)["items"]), 1
            )
            with _set_lake_uri(lake_uri), _set_data_dir(data_dir):
                self.assertEqual(load_records()["items"], [])

    def test_iceberg_and_parquet_reads_have_same_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            lake_uri = str(Path(tmp) / "lake")
            seed_lake_from_captures(
                [
                    {
                        "provider": "offline_fixtures",
                        "raw": b"<html>ai tools directory</html>",
                        "tools": SAMPLE_TOOLS,
                        "content_type": "text/html",
                        "source_page_url": "https://example.com/directory",
                    }
                ],
                data_lake_uri=lake_uri,
            )

            def read_catalog_rows(contract, dataset, *, data_lake_uri=None, sql=None):
                return lake.read_bronze_rows(
                    dataset, data_lake_uri=data_lake_uri, sql=sql
                )

            with _set_lake_uri(lake_uri), _set_read_mode("parquet"):
                expected_tools = load_records()
                expected_history = load_history()
            with (
                _set_lake_uri(lake_uri),
                _set_read_mode("iceberg"),
                mock.patch(
                    "data_lake.product_store.read_iceberg_rows",
                    side_effect=read_catalog_rows,
                ),
            ):
                actual_tools = load_records()
                actual_history = load_history()

            self.assertEqual(actual_tools["items"], expected_tools["items"])
            self.assertEqual(actual_history["items"], expected_history["items"])
            self.assertEqual(actual_tools["source_kind"], "iceberg_rest_duckdb")
            self.assertEqual(actual_history["source_kind"], "iceberg_rest_duckdb")
            self.assertEqual(actual_tools["read_mode"], "iceberg")

    def test_iceberg_mode_fallback_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            lake_uri = str(Path(tmp) / "lake")
            seed_lake_from_captures(
                [
                    {
                        "provider": "offline_fixtures",
                        "raw": b"<html>ai tools directory</html>",
                        "tools": SAMPLE_TOOLS,
                        "content_type": "text/html",
                        "source_page_url": "https://example.com/directory",
                    }
                ],
                data_lake_uri=lake_uri,
            )
            with (
                _set_lake_uri(lake_uri),
                _set_read_mode("iceberg", "parquet"),
                mock.patch(
                    "data_lake.product_store.read_iceberg_rows",
                    side_effect=lake.LakeUnavailable("catalog unavailable"),
                ),
            ):
                payload = load_records()

            self.assertGreaterEqual(len(payload["items"]), 1)
            self.assertEqual(payload["source_kind"], "bronze_parquet_fallback")
            self.assertIn("catalog unavailable", payload["fallback_reason"])

    def test_iceberg_mode_fails_closed_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            lake_uri = str(Path(tmp) / "lake")
            seed_lake_from_captures(
                [
                    {
                        "provider": "offline_fixtures",
                        "raw": b"<html>ai tools directory</html>",
                        "tools": SAMPLE_TOOLS,
                        "content_type": "text/html",
                        "source_page_url": "https://example.com/directory",
                    }
                ],
                data_lake_uri=lake_uri,
            )
            with (
                _set_lake_uri(lake_uri),
                _set_read_mode("iceberg", "error"),
                mock.patch(
                    "data_lake.product_store.read_iceberg_rows",
                    side_effect=lake.LakeUnavailable("catalog credentials unavailable"),
                ),
            ):
                payload = load_records()

            self.assertEqual(payload["data_status"], "error")
            self.assertEqual(payload["items"], [])
            self.assertEqual(payload["source_kind"], "iceberg_rest_duckdb")
            self.assertIn("catalog credentials unavailable", payload["error"])


class PolicyTests(unittest.TestCase):
    def test_public_directories_free(self):
        self.assertTrue(policy.evaluate_provider("public_directories").allowed)
        self.assertTrue(policy.evaluate_provider("offline_fixtures").allowed)

    def test_paid_blocked(self):
        with mock.patch.object(config, "FREE_ONLY", True):
            self.assertFalse(policy.evaluate_provider("firecrawl").allowed)
            self.assertFalse(policy.evaluate_provider("brave_search_api").allowed)
            self.assertFalse(policy.evaluate_provider("paid_search").allowed)


@unittest.skipUnless(
    _lake_deps_available(),
    "pyarrow/duckdb + shared data_lake runtime required (pip install -e .[lake])",
)
class ApiContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.data_dir = root / "data"
        self.data_dir.mkdir()
        self.lake_uri = str(root / "lake")
        seed_lake_from_captures(
            [
                {
                    "provider": "public_directories",
                    "raw": b"<html>tools</html>",
                    "tools": SAMPLE_TOOLS,
                    "content_type": "text/html",
                    "source_page_url": "https://example.com/dir",
                }
            ],
            data_lake_uri=self.lake_uri,
            data_dir=self.data_dir,
        )
        self.data_patch = _set_data_dir(self.data_dir)
        self.lake_patch = _set_lake_uri(self.lake_uri)
        self.read_patch = _set_read_mode("parquet", "error")
        self.data_patch.start()
        self.lake_patch.start()
        self.read_patch.start()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), DataProductHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.lake_patch.stop()
        self.data_patch.stop()
        self.read_patch.stop()
        self.tmp.cleanup()

    def _get(self, path: str):
        try:
            with urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def test_metadata(self):
        status, body = self._get("/v1/metadata")
        self.assertEqual(status, 200)
        meta = body["items"][0]
        self.assertEqual(meta["storage_model"], "lake_first_bronze_duckdb")
        self.assertEqual(meta["lake_domain"], "ai-tools")
        self.assertEqual(meta["capture_mode"], "per_provider_url")
        self.assertEqual(meta["records_source_kind"], "bronze_parquet")
        self.assertEqual(meta["read_mode"], "parquet")
        self.assertEqual(meta["read_fallback"], "error")

    def test_metadata_reports_iceberg_mode(self):
        def read_catalog_rows(contract, dataset, *, data_lake_uri=None, sql=None):
            return lake.read_bronze_rows(dataset, data_lake_uri=data_lake_uri, sql=sql)

        with (
            _set_read_mode("iceberg"),
            mock.patch(
                "data_lake.product_store.read_iceberg_rows",
                side_effect=read_catalog_rows,
            ),
        ):
            status, body = self._get("/v1/metadata")

        self.assertEqual(status, 200)
        meta = body["items"][0]
        self.assertEqual(meta["storage_model"], "lake_first_iceberg_rest_duckdb")
        self.assertEqual(meta["records_source_kind"], "iceberg_rest_duckdb")
        self.assertEqual(meta["history_source_kind"], "iceberg_rest_duckdb")
        self.assertEqual(meta["read_mode"], "iceberg")

    def test_api_without_csv(self):
        self.assertFalse(any(self.data_dir.glob("*.csv")))
        status, body = self._get("/v1/records")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(body["items"]), 1)
        (self.data_dir / config.RECORDS_FILE).write_text(
            "name,description,url,source,upvotes,category,pricing,tags,opportunity_tags,scraped_at\n"
            "FAKE,x,https://fake.example,f,1,c,p,t,,2099-01-01\n",
            encoding="utf-8",
        )
        status, body = self._get("/v1/records")
        urls = {i.get("url") or i.get("canonical_url") for i in body["items"]}
        self.assertTrue(any("example.com/ai-chat" in (u or "") for u in urls))
        self.assertFalse(any("fake.example" in (u or "") for u in urls))

    def test_refresh_forbidden(self):
        req = Request(
            f"http://127.0.0.1:{self.port}/v1/refresh", data=b"{}", method="POST"
        )
        try:
            urlopen(req, timeout=5)
            self.fail("expected 403")
        except HTTPError as e:
            self.assertEqual(e.code, 403)


class SmokeCompileTests(unittest.TestCase):
    def test_modules_import(self):
        from book_ai_tools import api, config as cfg, store  # noqa: F401 (import smoke)

        self.assertEqual(cfg.SCHEMA_VERSION, "ai_tools.v1")
        self.assertEqual(int(cfg.API_PORT), 8107)
        self.assertEqual(cfg.LAKE_DOMAIN, "ai-tools")
        self.assertEqual(cfg.LAKE_DATASET_TOOLS, "ai_tools")
        self.assertEqual(cfg.LAKE_READ_MODE, "parquet")
        self.assertEqual(cfg.LAKE_READ_FALLBACK, "error")
        self.assertTrue(hasattr(store, "load_csv_projection"))


if __name__ == "__main__":
    unittest.main()
