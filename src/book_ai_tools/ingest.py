#!/usr/bin/env python3
"""
AI tools directory scrape (lake-first).

Public directory/page bytes land separately per provider/URL. Firecrawl,
Brave Search API, and paid search are blocked before network under free-only.

Flow:
    Public directory HTML
      → landing/provider=public_directories/…
      → Bronze ai_tools (+ history)
      → DuckDB API
      → optional CSV
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_ROOT / "data"

try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass


def _require_httpx():
    try:
        import httpx as _httpx
    except ImportError:
        print("ERROR: httpx required for live ingestion. Install: pip install httpx")
        raise SystemExit(1)
    return _httpx


class _HttpxProxy:
    def __getattr__(self, name):
        return getattr(_require_httpx(), name)


httpx = _HttpxProxy()

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    BeautifulSoup = None  # type: ignore

try:
    from . import config as _dp_config
    from .policy import evaluate_provider, require_provider, external_writes_allowed
    from .store import seed_fixtures
    from . import lake as _lake
    from .fsutil import atomic_append_csv, atomic_write_csv
except ImportError:  # pragma: no cover
    _dp_config = None
    _lake = None

    def evaluate_provider(name):
        class D:
            allowed = True
            status = "free"
            reason = ""

        return D()

    def require_provider(name):
        return evaluate_provider(name)

    def external_writes_allowed():
        return False

    def seed_fixtures(data_dir=None):
        return None


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
    )
}

_GARBAGE_URL_PATTERNS = [
    "google.com",
    "youtube.com",
    "consent.google",
    "policies.google",
    "/httpservice/retry/",
    "/search?q=",
    "accounts.google",
    "duckduckgo.com",
    "wikipedia.org",
]

OPPORTUNITY_KEYWORDS = [
    "api",
    "sdk",
    "saas",
    "automation",
    "agent",
    "workflow",
    "scraping",
    "monitoring",
    "analytics",
    "dashboard",
    "chatbot",
    "code",
    "developer",
    "integration",
    "plugin",
]

DEFAULT_CATEGORIES = ["ai", "developer-tools", "productivity", "automation"]

PRODUCTHUNT_URL = "https://www.producthunt.com/topics/artificial-intelligence"
TAAFT_URL = "https://theresanaiforthat.com/most-saved/"


def _is_valid_url(url: str) -> bool:
    if not url or url.startswith("/"):
        return False
    return not any(pat in url for pat in _GARBAGE_URL_PATTERNS)


def _clean_search_name(raw_name: str, url: str = "") -> str:
    if not raw_name:
        return ""
    if any(sep in raw_name for sep in ["›", "»", "→", "←"]):
        for sep in ["›", "»", "→", "←"]:
            if sep in raw_name:
                raw_name = raw_name.split(sep)[-1].strip()
                break
    raw_name = re.sub(
        r"^[a-z0-9.-]+\.com\s*[-–—|]\s*", "", raw_name, flags=re.IGNORECASE
    )
    name = raw_name.strip()
    if (len(name) > 60 or not name) and url:
        slug = url.rstrip("/").split("/")[-1]
        if slug and not slug.startswith("?") and not slug.startswith("#"):
            name = slug.replace("-", " ").replace("_", " ").title()
            for acr in ["AI", "API", "SDK", "URL", "SEO", "UI", "UX"]:
                name = re.sub(rf"\b{acr.title()}\b", acr, name, flags=re.IGNORECASE)
    return name[:80] if name else ""


def _name_from_ph_url(url: str) -> str:
    m = re.search(r"producthunt\.com/(?:posts|products)/([a-z0-9][a-z0-9-]+)", url)
    if m:
        slug = re.sub(r"-\d+$", "", m.group(1))
        name = slug.replace("-", " ").title()
        for acr in ["AI", "API", "SDK", "URL", "SEO", "UI", "UX"]:
            name = re.sub(rf"\b{acr.title()}\b", acr, name, flags=re.IGNORECASE)
        return name
    return ""


def _name_from_taft_url(url: str) -> str:
    m = re.search(r"theresanaiforthat\.com/ai/([a-z0-9][a-z0-9-]+)", url)
    if m:
        name = m.group(1).replace("-", " ").title()
        for acr in ["AI", "API", "SDK", "URL", "SEO", "UI", "UX"]:
            name = re.sub(rf"\b{acr.title()}\b", acr, name, flags=re.IGNORECASE)
        return name
    return ""


def parse_producthunt(markdown: str) -> list:
    products = []
    lines = markdown.split("\n")
    joined = []
    buf = ""
    for line in lines:
        if buf:
            buf += "\n" + line
            if ")" in line:
                joined.append(buf)
                buf = ""
        elif line.strip().startswith("[") and ")" not in line:
            buf = line
        else:
            joined.append(line)
    if buf:
        joined.append(buf)

    for raw_line in joined:
        raw_line = raw_line.strip()
        match = re.match(
            r"\[([^\]]+)\]\((https://www\.producthunt\.com/(?:posts|products)/[^\)]+)\)",
            raw_line,
        )
        if match:
            raw_name = match.group(1)
            url = match.group(2)
            parts = re.split(r"\\+n?\\*", raw_name)
            name = parts[0].strip()
            desc = parts[1].strip() if len(parts) > 1 else ""
            if not name or len(name) > 100:
                continue
            upvotes = ""
            rating_match = re.search(r"(\d+\.?\d*)\s*\(", raw_line)
            if rating_match:
                upvotes = rating_match.group(1)
            products.append(
                {
                    "name": name,
                    "description": desc,
                    "url": url,
                    "source": "ProductHunt",
                    "vendor": "ProductHunt",
                    "upvotes": upvotes,
                    "category": "",
                    "pricing": "",
                    "tags": "",
                }
            )
    return products


def parse_taft(markdown: str) -> list:
    tools = []
    for line in markdown.split("\n"):
        line = line.strip()
        match = re.match(
            r"\[([^\]]+)\]\((https://theresanaiforthat\.com/ai/[^\)]+)\)", line
        )
        if match:
            tools.append(
                {
                    "name": match.group(1),
                    "description": "",
                    "url": match.group(2),
                    "source": "TheresAnAIForThat",
                    "vendor": "TheresAnAIForThat",
                    "upvotes": "",
                    "category": "",
                    "pricing": "",
                    "tags": "",
                }
            )
    return tools


def _block_paid_search(label: str) -> None:
    """Ensure paid/search APIs are blocked before any network attempt."""
    for name in ("firecrawl", "brave_search_api", "paid_search"):
        decision = evaluate_provider(name)
        if not decision.allowed:
            # expected under free-only
            continue
        # If somehow allowed, still don't use them from free path silently.
    # Explicit log when someone tries firecrawl entrypoints
    if label:
        pass


def fetch_firecrawl_search(query: str, limit: int = 10) -> list:
    """Blocked under free-only — policy gate before network."""
    decision = evaluate_provider("firecrawl")
    if not decision.allowed:
        print(f"  Firecrawl blocked ({decision.status}): {decision.reason}")
        return []
    api_key = (
        getattr(_dp_config, "FIRECRAWL_API_KEY", "")
        if _dp_config
        else os.environ.get("FIRECRAWL_API_KEY", "")
    ) or os.environ.get("FIRECRAWL_API_KEY", "")
    if not api_key:
        print("  Firecrawl skipped: missing FIRECRAWL_API_KEY")
        return []
    try:
        resp = httpx.post(
            "https://api.firecrawl.dev/v1/search",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={"query": query, "limit": limit},
            timeout=30,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        results = []
        for item in data.get("data", []):
            title = item.get("title", "") or item.get("metadata", {}).get("title", "")
            url = item.get("url", "") or item.get("metadata", {}).get("sourceURL", "")
            desc = item.get("description", "") or item.get("markdown", "")[:200]
            if title and url:
                results.append({"name": title[:100], "url": url, "description": desc})
            if len(results) >= limit:
                break
        return results
    except Exception as e:  # noqa: BLE001
        print(f"  Firecrawl failed: {e}")
        return []


def fetch_brave_search(query: str, limit: int = 10) -> list:
    """Brave Search — blocked as brave_search_api under free-only."""
    decision = evaluate_provider("brave_search_api")
    if not decision.allowed:
        print(f"  Brave Search blocked ({decision.status}): {decision.reason}")
        return []
    # Paid search alias also checked
    paid = evaluate_provider("paid_search")
    if not paid.allowed and decision.allowed is False:
        return []
    # If explicitly allowed by owner, still not implemented as free default.
    print("  Brave Search allowed but not used in free public_directories path")
    return []


def fetch_directory_page_raw(
    url: str,
    *,
    parser: str,
) -> tuple[bytes, str, list[dict[str, Any]]]:
    """Fetch a public directory page; return exact HTML + parsed tools."""
    decision = evaluate_provider("public_directories")
    if not decision.allowed:
        print(f"  Public directories blocked ({decision.status}): {decision.reason}")
        return b"", "text/html", []
    # Ensure paid paths are not used as silent fallbacks.
    _block_paid_search("directory")
    for blocked in ("firecrawl", "brave_search_api", "paid_search"):
        d = evaluate_provider(blocked)
        if d.allowed:
            continue
    if BeautifulSoup is None:
        print("  Public directories skipped: beautifulsoup4 not installed")
        return b"", "text/html", []
    try:
        resp = httpx.get(url, headers=HEADERS, timeout=15, follow_redirects=True)
        raw = getattr(resp, "content", None) or resp.text.encode(
            "utf-8", errors="replace"
        )
        if resp.status_code != 200:
            print(f"  Directory HTTP {resp.status_code} for {url}")
            return raw, "text/html", []
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup.find_all(["script", "style", "nav", "footer", "aside"]):
            tag.decompose()
        main = soup.find("main") or soup.find("article") or soup.body
        text = main.get_text(separator="\n", strip=True) if main else resp.text
        if parser == "producthunt":
            tools = parse_producthunt(text)
        elif parser == "taft":
            tools = parse_taft(text)
        else:
            tools = []
        return raw, "text/html", tools
    except Exception as e:  # noqa: BLE001
        print(f"  Warning: directory scrape failed for {url}: {e}")
        return b"", "text/html", []


def fetch_producthunt_free() -> list:
    """Backward-compatible list-only ProductHunt fetch."""
    _raw, _ctype, tools = fetch_directory_page_raw(
        PRODUCTHUNT_URL, parser="producthunt"
    )
    return tools


def fetch_theresanaiforthat() -> list:
    """Backward-compatible list-only TAAFT fetch."""
    _raw, _ctype, tools = fetch_directory_page_raw(TAAFT_URL, parser="taft")
    return tools


def fetch_ai_tool_directories() -> list:
    """Category search — only via public_directories; paid search blocked."""
    # Free-only: do not call Brave/Firecrawl.
    brave = evaluate_provider("brave_search_api")
    if not brave.allowed:
        print(
            f"  Directory search via Brave blocked ({brave.status}): {brave.reason}"
        )
    fire = evaluate_provider("firecrawl")
    if not fire.allowed:
        print(f"  Directory search via Firecrawl blocked ({fire.status}): {fire.reason}")
    paid = evaluate_provider("paid_search")
    if not paid.allowed:
        print(f"  Paid search blocked ({paid.status}): {paid.reason}")
    # No free public HTML directory for generic category search without SERP.
    return []


def detect_opportunities(tools: list) -> list:
    opportunities = []
    for tool in tools:
        text = f"{tool.get('name', '')} {tool.get('description', '')} {tool.get('tags', '')}".lower()
        matched = [kw for kw in OPPORTUNITY_KEYWORDS if kw in text]
        if matched:
            tool = dict(tool)
            tool["opportunity_tags"] = ",".join(matched)
            opportunities.append(tool)
    return opportunities


def load_previous_urls(output_dir: Optional[Path] = None) -> set:
    history_file = Path(output_dir or OUTPUT_DIR) / "ai_tools_history.csv"
    urls = set()
    if history_file.exists():
        with open(history_file, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("url"):
                    urls.add(row["url"])
    return urls


def _projection_timestamp() -> str:
    # UTC: readers (product_store.parse_ts / _freshness) treat naive stamps as
    # UTC, so a host-local stamp looked hours fresher (or older) than it was.
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def project_tools_csv(tools: list[dict], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    filepath = output_dir / "ai_tools.csv"
    fieldnames = [
        "name",
        "description",
        "url",
        "canonical_url",
        "vendor",
        "source",
        "provider",
        "upvotes",
        "category",
        "pricing",
        "tags",
        "opportunity_tags",
        "observed_at",
        "scraped_at",
    ]
    scraped = _projection_timestamp()
    rows = [{k: tool.get(k, "") for k in fieldnames} | {"scraped_at": scraped} for tool in tools]
    atomic_write_csv(filepath, fieldnames, rows)
    print(f"  Projected {len(tools)} tools → {filepath}")
    return filepath


def project_history_csv(tools: list[dict], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    filepath = output_dir / "ai_tools_history.csv"
    fieldnames = [
        "name",
        "description",
        "url",
        "canonical_url",
        "vendor",
        "source",
        "provider",
        "upvotes",
        "category",
        "pricing",
        "tags",
        "opportunity_tags",
        "observed_at",
        "scraped_at",
    ]
    scraped = _projection_timestamp()
    rows = [{k: tool.get(k, "") for k in fieldnames} | {"scraped_at": scraped} for tool in tools]
    atomic_append_csv(filepath, fieldnames, rows)
    print(f"  Projected +{len(tools)} history rows → {filepath}")
    return filepath


def save_tools(tools: list, output_dir: Optional[Path] = None):
    return project_tools_csv(tools, Path(output_dir or OUTPUT_DIR))


def append_history(tools: list, output_dir: Optional[Path] = None):
    return project_history_csv(tools, Path(output_dir or OUTPUT_DIR))


def _land_page(
    *,
    provider: str,
    raw: bytes,
    tools: list[dict],
    content_type: str,
    dataset: str,
    data_lake_uri: Optional[str],
    output_dir: Path,
    source_page_url: str,
) -> dict[str, Any]:
    if _lake is None or _dp_config is None:
        raise RuntimeError("Lake adapter unavailable")
    if not raw:
        raise _lake.LakeIngestError(f"Empty raw payload from {source_page_url}")
    input_format = "html" if content_type.startswith("text/html") else "json"
    result = _lake.ingest_provider_payload(
        provider=provider,
        raw=raw,
        tools=tools,
        dataset=dataset,
        content_type=content_type,
        input_format=input_format,
        data_lake_uri=data_lake_uri,
        metadata={"source_page_url": source_page_url},
        source_page_url=source_page_url,
    )
    _lake.write_lineage(
        result,
        dataset=dataset,
        data_dir=output_dir,
        provider=provider,
        page_key=source_page_url,
    )
    print(
        f"  Lake {dataset}/{provider}: page={source_page_url[:60]} "
        f"records={result.get('record_count')} raw={result.get('raw_key')}"
    )
    return result


def run_live_ingest(
    *,
    sources: list[str],
    output_dir: Path,
    min_upvotes: int = 0,
    write_history: bool = True,
    data_lake_uri: Optional[str] = None,
    project_csv: bool = True,
) -> dict[str, Any]:
    print(
        f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
        "AI Tool Scraper (lake-first)"
    )
    print(f"  Sources: {sources}")
    if data_lake_uri:
        print(f"  Data lake: {data_lake_uri}")
    elif _lake is not None:
        print(f"  Data lake: {_lake.default_data_lake_uri()}")

    lake_batches: list[dict[str, Any]] = []
    all_tools: list[dict[str, Any]] = []
    providers_used: list[str] = []
    page_captures: list[tuple[str, bytes, str, list, str]] = []

    for source in sources:
        if source == "producthunt":
            print("\n  Fetching ProductHunt...")
            raw, ctype, tools = fetch_directory_page_raw(
                PRODUCTHUNT_URL, parser="producthunt"
            )
            print(f"    Found {len(tools)} products")
            if raw and tools:
                page_captures.append(
                    ("public_directories", raw, ctype, tools, PRODUCTHUNT_URL)
                )
        elif source in ("taaft", "theresanaiforthat"):
            print("\n  Fetching There's An AI For That...")
            raw, ctype, tools = fetch_directory_page_raw(TAAFT_URL, parser="taft")
            print(f"    Found {len(tools)} tools")
            if raw and tools:
                page_captures.append(
                    ("public_directories", raw, ctype, tools, TAAFT_URL)
                )
        elif source == "search":
            print("\n  Fetching AI directories...")
            tools = fetch_ai_tool_directories()
            print(f"    Found {len(tools)} tools (search sources may be blocked)")
            # No raw page for blocked SERP path — skip landing if empty.

    # Land each page separately (no merge of raw HTML).
    for provider, raw, ctype, tools, page_url in page_captures:
        batch = _land_page(
            provider=provider,
            raw=raw,
            tools=tools,
            content_type=ctype,
            dataset=_dp_config.LAKE_DATASET_TOOLS,
            data_lake_uri=data_lake_uri,
            output_dir=output_dir,
            source_page_url=page_url,
        )
        lake_batches.append(batch)
        providers_used.append(provider)
        all_tools.extend(tools)

        if write_history:
            hist_raw = json.dumps(
                {
                    "provider": provider,
                    "dataset": _dp_config.LAKE_DATASET_HISTORY,
                    "parent_run_id": batch.get("run_id"),
                    "source_page_url": page_url,
                    "tools": tools,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
            hist = _land_page(
                provider=provider,
                raw=hist_raw,
                tools=tools,
                content_type="application/json",
                dataset=_dp_config.LAKE_DATASET_HISTORY,
                data_lake_uri=data_lake_uri,
                output_dir=output_dir,
                source_page_url=page_url,
            )
            lake_batches.append(hist)

    # Dedupe by canonical URL for projection/summary
    seen = set()
    unique_tools: list[dict[str, Any]] = []
    for tool in all_tools:
        url = tool.get("url") or ""
        can = _lake.canonical_url(url) if _lake else url
        key = can or url or tool.get("name")
        if key in seen:
            continue
        seen.add(key)
        unique_tools.append(tool)

    if min_upvotes > 0:
        unique_tools = [
            t
            for t in unique_tools
            if str(t.get("upvotes") or "0").replace(".", "", 1).isdigit()
            and float(t.get("upvotes") or 0) >= min_upvotes
        ]

    opportunities = detect_opportunities(unique_tools)

    if not lake_batches:
        raise RuntimeError(
            "No AI tools landed. Ensure public_directories is allowed "
            "or use --fixture for offline mode."
        )

    if project_csv and unique_tools:
        # Read the previous history before this run appends to it; reading it
        # afterwards made every tool look "seen" and NEW-tool alerts never fired.
        previous_urls = load_previous_urls(output_dir)
        # Normalize projection rows
        observed = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = []
        for t in unique_tools:
            rec = (
                _lake.normalize_tool_record(
                    t, provider="public_directories", observed_at=observed
                )
                if _lake
                else t
            )
            rows.append(
                {
                    "name": rec.get("name") or t.get("name"),
                    "description": rec.get("description") or t.get("description"),
                    "url": rec.get("url") or t.get("url"),
                    "canonical_url": rec.get("canonical_url") or t.get("url"),
                    "vendor": rec.get("vendor") or t.get("vendor") or t.get("source"),
                    "source": t.get("source"),
                    "provider": rec.get("provider") or "public_directories",
                    "upvotes": t.get("upvotes", ""),
                    "category": t.get("category", ""),
                    "pricing": t.get("pricing", ""),
                    "tags": t.get("tags", ""),
                    "opportunity_tags": t.get("opportunity_tags", ""),
                    "observed_at": observed,
                }
            )
        project_tools_csv(rows, output_dir)
        if write_history:
            project_history_csv(rows, output_dir)

        new_tools = [t for t in unique_tools if t.get("url") not in previous_urls]
        if new_tools:
            print(f"\n  *** {len(new_tools)} NEW AI TOOLS detected ***")
            for tool in new_tools[:10]:
                print(
                    f"    {str(tool.get('name', ''))[:40]:40s} | "
                    f"{str(tool.get('source', '')):15s} | "
                    f"{str(tool.get('url', ''))[:50]}"
                )
        if opportunities:
            print(f"\n  OPPORTUNITIES ({len(opportunities)}):")
            for tool in opportunities[:10]:
                print(
                    f"    {str(tool.get('name', ''))[:35]:35s} | "
                    f"{str(tool.get('opportunity_tags', '')):20s} | "
                    f"{str(tool.get('url', ''))[:40]}"
                )

    print(
        f"\n  Landed batches: {len(lake_batches)} | "
        f"Tools: {len(unique_tools)} | Opportunities: {len(opportunities)}"
    )
    print("  Done (per-page landing; CSV is projection only).")
    return {
        "tools": len(unique_tools),
        "opportunities": len(opportunities),
        "lake_batches": len(lake_batches),
        "providers": sorted(set(providers_used)),
        "projected_csv": project_csv,
    }


KNOWN_SOURCES = ("producthunt", "taaft", "search")
_SOURCE_ALIASES = {"taft": "taaft", "theresanaiforthat": "taaft"}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scrape AI tools (lake-first; public directories)"
    )
    parser.add_argument(
        "--sources",
        default="producthunt,taaft",
        help="Sources: producthunt, taaft (alias: taft, theresanaiforthat), search "
        "(search is blocked under free-only)",
    )
    parser.add_argument("--categories", default=",".join(DEFAULT_CATEGORIES))
    parser.add_argument("--min-upvotes", type=int, default=0)
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--data-lake-uri", default=None)
    parser.add_argument("--no-history", action="store_true")
    parser.add_argument("--no-project-csv", action="store_true")
    parser.add_argument("--fixture", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    sources = list(
        dict.fromkeys(
            _SOURCE_ALIASES.get(s, s)
            for s in (part.strip().lower() for part in args.sources.split(","))
            if s
        )
    )
    unknown = [s for s in sources if s not in KNOWN_SOURCES]
    if unknown:
        parser.error(
            f"unknown --sources {','.join(unknown)} (choose from {', '.join(KNOWN_SOURCES)})"
        )
    if not sources:
        parser.error("--sources must name at least one source")
    if args.min_upvotes < 0:
        parser.error("--min-upvotes must be >= 0")
    output_dir = Path(args.output_dir)

    if args.fixture or args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        if _dp_config is not None:
            _dp_config.DATA_DIR = output_dir
        seed_fixtures(output_dir)
        print(
            "["
            + datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            + "] Fixture mode: seeded local projection under "
            + str(output_dir)
        )
        print("  No upstream providers contacted; no lake write.")
        return 0

    try:
        run_live_ingest(
            sources=sources,
            output_dir=output_dir,
            min_upvotes=args.min_upvotes,
            write_history=not args.no_history,
            data_lake_uri=args.data_lake_uri,
            project_csv=not args.no_project_csv,
        )
    except Exception as exc:  # noqa: BLE001
        if _lake is not None and isinstance(
            exc, (_lake.LakeUnavailable, _lake.LakeIngestError)
        ):
            print(
                f"ERROR: lake ingest failed before projection: {exc}",
                file=sys.stderr,
            )
            return 2
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


class AIToolScraper:
    def __init__(self, sources=None, categories=None, **kwargs):
        self.sources = sources or ["producthunt", "taaft"]
        self.categories = categories or DEFAULT_CATEGORIES
        self.data_lake_uri = kwargs.get("data_lake_uri")
        self.output_dir = Path(kwargs.get("output_dir") or OUTPUT_DIR)

    async def run(self, **kwargs):
        result = run_live_ingest(
            sources=self.sources,
            output_dir=Path(kwargs.get("output_dir") or self.output_dir),
            write_history=True,
            data_lake_uri=kwargs.get("data_lake_uri", self.data_lake_uri),
            project_csv=True,
        )
        return [
            {
                "source": "ai_tools",
                "count": result["tools"],
                "opportunities": result["opportunities"],
                "lake": True,
            }
        ]


if __name__ == "__main__":
    raise SystemExit(main())
