#!/usr/bin/env python3
"""Capture AI tool listings from Futurepedia's public HTML directory.

Futurepedia does not expose a source-specific API in this workflow. The
directory renders bounded category pages with tool cards, so this adapter
uses public HTML, validates canonical Futurepedia tool URLs, and preserves
the category/source attribution needed by downstream discovery consumers.
"""

from __future__ import annotations

import csv
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

try:
    import httpx
    from bs4 import BeautifulSoup
except ImportError as exc:  # pragma: no cover - requirements.txt supplies both
    raise RuntimeError("httpx and beautifulsoup4 are required for AI tool capture") from exc


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "data" / "exported"
BASE_URL = "https://www.futurepedia.io/ai-tools"
SOURCE_NAME = "Futurepedia"
DEFAULT_CATEGORIES = ["ai-agents", "productivity", "code"]
DEFAULT_MAX_PAGES = 2
DEFAULT_LIMIT = 100
MAX_CATEGORIES = 5
MAX_PAGES = 3
MAX_LIMIT = 200
MIN_ROWS = 10
ALLOWED_HOST = re.compile(r"(?:www\.)?futurepedia\.io", re.IGNORECASE)
CATEGORY_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
TOOL_PATH_RE = re.compile(r"^/tool/([a-z0-9]+(?:-[a-z0-9]+)*)$")
RATING_RE = re.compile(r"Rated\s+([0-9]+(?:\.[0-9]+)?)\s+out of 5", re.IGNORECASE)
RATING_COUNT_RE = re.compile(r"\(\s*([0-9][0-9,]*)\s*\)")
BOOKMARK_RE = re.compile(r"\b([0-9][0-9,]*)\s+Add bookmark\b", re.IGNORECASE)
PRICE_LABELS = (
    "Free Trial",
    "Contact for Pricing",
    "Freemium",
    "Open Source",
    "Free and Paid",
    "Paid",
    "Free",
)
SNAPSHOT_FIELDS = [
    "captured_at",
    "tool_id",
    "name",
    "description",
    "url",
    "website_url",
    "categories",
    "source_category",
    "rating",
    "rating_count",
    "bookmark_count",
    "pricing",
    "image_url",
    "source",
    "source_url",
    "page_number",
]
HISTORY_FIELDS = [
    "captured_at",
    "tool_id",
    "name",
    "url",
    "categories",
    "source_category",
    "rating",
    "rating_count",
    "bookmark_count",
    "pricing",
    "source",
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean_text(value: Any, limit: int = 1000) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _as_values(values: Iterable[str] | str | None, default: list[str]) -> list[str]:
    if values is None:
        values = default
    if isinstance(values, str):
        values = values.split(",")
    result: list[str] = []
    for value in values:
        normalized = str(value).strip().casefold()
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def normalize_categories(values: Iterable[str] | str | None) -> list[str]:
    categories = _as_values(values, DEFAULT_CATEGORIES)
    if not categories or len(categories) > MAX_CATEGORIES:
        raise ValueError(f"categories must contain 1-{MAX_CATEGORIES} Futurepedia paths")
    invalid = [category for category in categories if not CATEGORY_RE.fullmatch(category)]
    if invalid:
        raise ValueError(f"invalid Futurepedia category path(s): {', '.join(invalid)}")
    return categories


def normalize_base_url(value: str = BASE_URL) -> str:
    parts = urlsplit(str(value).strip())
    host = (parts.hostname or "").lower()
    path = parts.path.rstrip("/") or "/"
    if parts.scheme.lower() != "https" or not ALLOWED_HOST.fullmatch(host) or path != "/ai-tools":
        raise ValueError("Futurepedia base URL must be an HTTPS /ai-tools page")
    return urlunsplit(("https", host, path, "", ""))


def normalize_category_url(value: str, base_url: str = BASE_URL) -> str:
    base = normalize_base_url(base_url)
    category = str(value).strip().strip("/").split("/")[-1]
    if not CATEGORY_RE.fullmatch(category):
        raise ValueError("Futurepedia category must be a lowercase slug")
    return f"{base}/{category}"


def build_page_url(base_url: str, category: str, page_number: int) -> str:
    if isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 1:
        raise ValueError("page_number must be a positive integer")
    category_url = normalize_category_url(category, base_url)
    return f"{category_url}?page={page_number}"


def canonical_tool_url(value: str) -> str:
    parts = urlsplit(str(value).strip())
    host = (parts.hostname or "").lower()
    path = parts.path.rstrip("/") or "/"
    match = TOOL_PATH_RE.fullmatch(path)
    if parts.scheme.lower() != "https" or not ALLOWED_HOST.fullmatch(host) or not match:
        raise ValueError("Futurepedia tool URL must be an HTTPS /tool/<slug> URL")
    return urlunsplit(("https", host, path, "", ""))


def _external_url(value: str | None) -> str:
    if not value:
        return ""
    parts = urlsplit(str(value).strip())
    if parts.scheme.lower() != "https" or not parts.hostname:
        return ""
    return urlunsplit(("https", parts.hostname.lower(), parts.path.rstrip("/") or "/", "", ""))


def _card_for(anchor: Any) -> Any:
    node = anchor
    while node is not None:
        classes = node.get("class") or []
        if "shadow-lg" in classes:
            return node
        node = node.parent
    return None


def _rating(card: Any) -> tuple[float | str, int]:
    text = card.get_text(" ", strip=True)
    match = RATING_RE.search(text)
    rating: float | str = ""
    if match:
        number = float(match.group(1))
        if math.isfinite(number) and 0 <= number <= 5:
            rating = number
    count_match = RATING_COUNT_RE.search(text)
    rating_count = int(count_match.group(1).replace(",", "")) if count_match else 0
    return rating, rating_count


def _pricing(card: Any) -> str:
    text = card.get_text(" ", strip=True)
    for label in PRICE_LABELS:
        if re.search(rf"\b{re.escape(label)}\b", text, re.IGNORECASE):
            return label
    return ""


def _categories(card: Any) -> str:
    values: list[str] = []
    for anchor in card.select("a[href]"):
        href = str(anchor.get("href") or "")
        path = urlsplit(href).path
        if not path.startswith("/ai-tools/"):
            continue
        label = _clean_text(anchor.get_text(" ", strip=True), 100).lstrip("#").strip()
        if label and label not in values:
            values.append(label)
    return ",".join(values[:10])


def _bookmark_count(card: Any) -> int:
    match = BOOKMARK_RE.search(card.get_text(" ", strip=True))
    return int(match.group(1).replace(",", "")) if match else 0


def parse_html(
    html: str,
    source_url: str,
    source_category: str,
    page_number: int = 1,
) -> list[dict[str, Any]]:
    """Parse Futurepedia cards, keeping canonical tool URLs only."""

    normalized_source = normalize_category_url(source_category, normalize_base_url(source_url.split("/ai-tools")[0] + "/ai-tools"))
    category = normalized_source.rsplit("/", 1)[-1]
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for anchor in soup.select('a[href*="/tool/"]'):
        try:
            url = canonical_tool_url(anchor.get("href", ""))
        except ValueError:
            continue
        tool_id = url.rsplit("/", 1)[-1]
        if tool_id in seen:
            continue
        card = _card_for(anchor)
        if card is None:
            continue
        title = card.select_one('a[href*="/tool/"] p')
        name = _clean_text(title.get_text(" ", strip=True) if title else anchor.get_text(" ", strip=True), 300)
        if not name:
            continue
        description_node = card.select_one("p.text-muted-foreground")
        website_anchor = next(
            (candidate for candidate in card.select("a[href]") if _clean_text(candidate.get_text(" ", strip=True)).casefold() == "visit"),
            None,
        )
        image = card.find("img")
        rating, rating_count = _rating(card)
        rows.append(
            {
                "tool_id": tool_id,
                "name": name,
                "description": _clean_text(description_node.get_text(" ", strip=True) if description_node else "", 1000),
                "url": url,
                "website_url": _external_url(website_anchor.get("href") if website_anchor else None),
                "categories": _categories(card),
                "source_category": category,
                "rating": rating,
                "rating_count": rating_count,
                "bookmark_count": _bookmark_count(card),
                "pricing": _pricing(card),
                "image_url": _external_url(image.get("src") if image else None),
                "source": SOURCE_NAME,
                "source_url": normalized_source,
                "page_number": page_number,
            }
        )
        seen.add(tool_id)
    return rows


def _dedupe_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        key = str(row.get("tool_id") or row.get("url") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def fetch_pages(
    base_url: str,
    categories: list[str],
    max_pages: int,
    limit: int,
    min_rows: int = MIN_ROWS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or not 1 <= max_pages <= MAX_PAGES:
        raise ValueError(f"max_pages must be an integer from 1 to {MAX_PAGES}")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be an integer from 1 to {MAX_LIMIT}")
    if isinstance(min_rows, bool) or not isinstance(min_rows, int) or not 1 <= min_rows <= limit:
        raise ValueError(f"min_rows must be an integer from 1 to {limit}")
    normalized_base = normalize_base_url(base_url)
    raw_pages: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    for category in categories:
        for page_number in range(1, max_pages + 1):
            page_url = build_page_url(normalized_base, category, page_number)
            response = httpx.get(
                page_url,
                headers={
                    "User-Agent": "book-job-scraping/1.0",
                    "Accept": "text/html,application/xhtml+xml",
                },
                timeout=30,
                follow_redirects=True,
            )
            response.raise_for_status()
            if not response.content:
                raise ValueError(f"Futurepedia page {category}/{page_number} response is empty")
            page_rows = parse_html(response.text, normalized_base, category, page_number)
            raw_pages.append({"category": category, "page_number": page_number, "url": str(response.url), "html": response.text})
            rows.extend(page_rows)
    rows = _dedupe_rows(rows)[:limit]
    if len(rows) < min_rows:
        raise ValueError(f"Futurepedia capture produced only {len(rows)} tools; need at least {min_rows}")
    return raw_pages, rows


def write_raw(raw_pages: list[dict[str, Any]], output_dir: Path, stem: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{stem}_raw.json"
    path.write_text(json.dumps(raw_pages, ensure_ascii=False), encoding="utf-8")
    return path


def write_snapshot(rows: list[dict[str, Any]], captured_at: str, output_dir: Path, stem: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{stem}.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SNAPSHOT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "captured_at": captured_at})
    return path


def append_history(rows: list[dict[str, Any]], captured_at: str, output_dir: Path, stem: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{stem}_history.csv"
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=HISTORY_FIELDS, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        for row in rows:
            writer.writerow({**row, "captured_at": captured_at})
    return path


class AIToolScraper:
    """Scheduler adapter for bounded Futurepedia AI tool capture."""

    def __init__(
        self,
        sources: Iterable[str] | str | None = None,
        categories: Iterable[str] | str | None = None,
        max_pages: int = DEFAULT_MAX_PAGES,
        limit: int = DEFAULT_LIMIT,
        min_rows: int = MIN_ROWS,
        output_stem: str = "ai_tools",
        base_url: str = BASE_URL,
        output_dir: str | Path | None = None,
        **_: Any,
    ) -> None:
        requested_sources = _as_values(sources, ["futurepedia"])
        if requested_sources != ["futurepedia"]:
            raise ValueError("AIToolScraper currently supports only the futurepedia source")
        self.categories = normalize_categories(categories)
        if isinstance(max_pages, bool) or not isinstance(max_pages, int) or not 1 <= max_pages <= MAX_PAGES:
            raise ValueError(f"max_pages must be an integer from 1 to {MAX_PAGES}")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
            raise ValueError(f"limit must be an integer from 1 to {MAX_LIMIT}")
        if isinstance(min_rows, bool) or not isinstance(min_rows, int) or not 1 <= min_rows <= limit:
            raise ValueError(f"min_rows must be an integer from 1 to {limit}")
        if not re.fullmatch(r"[a-z0-9_]+", output_stem):
            raise ValueError("output_stem must contain only lowercase letters, numbers, and underscores")
        self.max_pages = max_pages
        self.limit = limit
        self.min_rows = min_rows
        self.output_stem = output_stem
        self.base_url = normalize_base_url(base_url)
        self.output_dir = Path(output_dir) if output_dir else OUTPUT_DIR

    async def run(self, max_pages: int | None = None, **_: Any) -> list[dict[str, Any]]:
        effective_max_pages = self.max_pages if max_pages is None else max_pages
        raw_pages, rows = fetch_pages(
            self.base_url,
            self.categories,
            max_pages=effective_max_pages,
            limit=self.limit,
            min_rows=self.min_rows,
        )
        captured_at = _utc_now()
        raw_path = write_raw(raw_pages, self.output_dir, self.output_stem)
        snapshot_path = write_snapshot(rows, captured_at, self.output_dir, self.output_stem)
        history_path = append_history(rows, captured_at, self.output_dir, self.output_stem)
        print(f"[{self.output_stem}] {len(rows)} tools -> {snapshot_path}")
        return [
            {
                "source": self.output_stem,
                "count": len(rows),
                "output": str(snapshot_path),
                "history": str(history_path),
                "raw": str(raw_path),
            }
        ]


if __name__ == "__main__":
    import asyncio

    asyncio.run(AIToolScraper().run())
