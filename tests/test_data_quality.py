"""Data-quality guards, new-tool detection and CLI validation for book-ai-tools-data."""
from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from book_ai_tools import ingest, lake
from book_ai_tools.fsutil import atomic_write_csv

OBSERVED = "2026-08-01T12:00:00Z"


def _tool(name: str, url: str, **extra) -> dict:
    return {"name": name, "url": url, "source": "ProductHunt", "description": "", **extra}


class ToolRecordQualityTests(unittest.TestCase):
    def test_unsafe_url_schemes_are_not_stored(self):
        for url in ("javascript:alert(1)", " JavaScript:void(0)", "data:text/html,x", "file:///etc/passwd"):
            with self.subTest(url=url):
                self.assertEqual(lake.canonical_url(url), "")
                rec = lake.normalize_tool_record(
                    _tool("Tool", url), provider="public_directories", observed_at=OBSERVED
                )
                self.assertEqual((rec["url"], rec["canonical_url"]), ("", ""))

    def test_unsafe_url_without_name_is_dropped(self):
        records = lake.tool_records_from_list(
            [_tool("", "javascript:alert(1)"), _tool("Real", "https://www.example.com/app/")],
            provider="public_directories",
            observed_at=OBSERVED,
        )
        self.assertEqual([r["canonical_url"] for r in records], ["https://example.com/app"])

    def test_upvotes_must_be_non_negative_numbers(self):
        cases = {"12": "12", "-3": "", "nan": "", "lots": "", "": "", None: None, 7: 7}
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                rec = lake.normalize_tool_record(
                    _tool("T", "https://t.example", upvotes=raw),
                    provider="public_directories",
                    observed_at=OBSERVED,
                )
                self.assertEqual(rec["upvotes"], expected)

    def test_ids_unchanged_for_valid_urls(self):
        rec = lake.normalize_tool_record(
            _tool("T", "https://www.Example.com/a/?ref=x"), provider="p", observed_at=OBSERVED
        )
        self.assertEqual(rec["id"], "example.com_a")


@unittest.skipUnless(
    lake.shared_runtime_available(),
    "shared data_lake runtime not installed (pip install -e .[lake])",
)
class NewToolDetectionTests(unittest.TestCase):
    """NEW-tool alerts must compare against history from *before* this run."""

    def _run(self, tmp: Path, tools: list[dict]) -> str:
        buf = io.StringIO()
        # Distinct raw bytes per run: identical bytes map to the same landing batch.
        raw = ("<html>" + ",".join(t["url"] for t in tools) + "</html>").encode()
        with mock.patch.object(
            ingest, "fetch_directory_page_raw", return_value=(raw, "text/html", tools)
        ), contextlib.redirect_stdout(buf):
            ingest.run_live_ingest(
                sources=["producthunt"], output_dir=tmp, data_lake_uri=str(tmp / "lake")
            )
        return buf.getvalue()

    def test_new_tools_reported_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            first = self._run(out, [_tool("A", "https://a.example/x")])
            self.assertIn("1 NEW AI TOOLS", first)
            second = self._run(out, [_tool("A", "https://a.example/x"), _tool("B", "https://b.example/y")])
            self.assertIn("1 NEW AI TOOLS", second)
            third = self._run(out, [_tool("B", "https://b.example/y"), _tool("A", "https://a.example/x")])
            self.assertNotIn("NEW AI TOOLS", third)


class CliValidationTests(unittest.TestCase):
    def _exit_code(self, argv: list[str]) -> int:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as ctx:
            ingest.main(argv)
        return ctx.exception.code

    def test_rejects_unknown_or_empty_sources_and_negative_upvotes(self):
        self.assertEqual(self._exit_code(["--sources", "producthunt,nope"]), 2)
        self.assertEqual(self._exit_code(["--sources", " , "]), 2)
        self.assertEqual(self._exit_code(["--min-upvotes", "-1"]), 2)

    def test_source_aliases_normalized(self):
        with mock.patch.object(ingest, "run_live_ingest") as run:
            self.assertEqual(ingest.main(["--sources", "ProductHunt,taft,theresanaiforthat"]), 0)
        self.assertEqual(run.call_args.kwargs["sources"], ["producthunt", "taaft"])


class AtomicProjectionTests(unittest.TestCase):
    def test_projection_ignores_extra_keys_and_leaves_no_temp_files(self):
        rows = [{**_tool("A", "https://a.example"), "unexpected": 1}]
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            out = Path(tmp)
            ingest.project_tools_csv(rows, out)
            ingest.project_history_csv(rows, out)
            ingest.project_history_csv(rows, out)
            self.assertEqual(sorted(p.name for p in out.iterdir()), ["ai_tools.csv", "ai_tools_history.csv"])
            self.assertEqual(len((out / "ai_tools_history.csv").read_text(encoding="utf-8").splitlines()), 3)

    def test_failed_write_keeps_previous_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.csv"
            atomic_write_csv(path, ["a"], [{"a": 1}])
            with self.assertRaises(ValueError):
                atomic_write_csv(path, ["a"], [{"b": 2}])
            self.assertEqual(path.read_bytes(), b"a\r\n1\r\n")


if __name__ == "__main__":
    unittest.main()
