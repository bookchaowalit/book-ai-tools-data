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

# Provider classes are loaded only for a real collection. This keeps the
# dependency-free dry-run path usable on an offline control-plane host.
JOBS = (("ai_tools", None),)


async def run_ai_tools(
    output_dir: Path, max_pages: int = 2, *, dry_run: bool = False
) -> list[dict[str, Any]]:
    if dry_run:
        return [
            {
                "job": name,
                "status": "dry-run",
                "source": "futurepedia",
                "max_pages": max_pages,
                "network": "not-used",
                "writes": "not-used",
            }
            for name, _cls in JOBS
        ]
    results: list[dict[str, Any]] = []
    from scrape_ai_tools import AIToolScraper

    for name, cls in JOBS:
        scraper = (cls or AIToolScraper)(
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
    parser = argparse.ArgumentParser(
        description="Run book-ai-tools-data Futurepedia collection"
    )
    parser.add_argument("--max-pages", type=int, default=2)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data" / "exported")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the bounded job plan without collection or writes",
    )
    args = parser.parse_args()
    results = asyncio.run(
        run_ai_tools(args.output_dir, args.max_pages, dry_run=args.dry_run)
    )
    if args.json:
        print(json.dumps(results, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
