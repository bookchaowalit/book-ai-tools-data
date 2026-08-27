#!/usr/bin/env python3
"""Run the bounded Futurepedia collector owned by this repository."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from scrape_ai_tools import AIToolScraper

JOBS = (("ai_tools", AIToolScraper),)


async def run_ai_tools(output_dir: Path, max_pages: int = 2) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for name, cls in JOBS:
        scraper = cls(
            sources=["futurepedia"],
            categories=["ai-agents", "productivity", "code"],
            max_pages=max_pages,
            limit=100,
            min_rows=10 if max_pages >= 2 else 5,
            output_dir=output_dir,
        )
        batch = await scraper.run()
        results.extend(batch)
        print(f"[run_ai_tools] {name}: {batch[0].get('count') if batch else 0}")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Run book-ai-tools-data Futurepedia collection")
    parser.add_argument("--max-pages", type=int, default=2)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data" / "exported")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    results = asyncio.run(run_ai_tools(args.output_dir, args.max_pages))
    if args.json:
        print(json.dumps(results, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
