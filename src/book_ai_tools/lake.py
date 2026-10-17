"""Lake-first adapter for book-ai-tools-data (per provider/URL landing)."""

from __future__ import annotations

import hashlib
import importlib.util
import re
import sys
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse, urlunparse

from . import config
from . import quality


def _load_shared():
    """Import the shared product adapter.

    Uses the installed ``solo-empire-data-lake`` package (the ``[lake]`` extra).
    Falls back to a data-lake checkout named by ``SOLO_DATA_LAKE_ROOT``.
    """
    if importlib.util.find_spec("data_lake") is None and config.SOLO_DATA_LAKE_ROOT:
        scripts = (
            Path(config.SOLO_DATA_LAKE_ROOT).expanduser().resolve()
            / "infra"
            / "scripts"
        )
        if not (scripts / "data_lake" / "product_adapter.py").is_file():
            raise ImportError(
                f"SOLO_DATA_LAKE_ROOT={config.SOLO_DATA_LAKE_ROOT} has no "
                "infra/scripts/data_lake/product_adapter.py"
            )
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
    try:
        from data_lake import product_adapter as pa  # type: ignore
    except ImportError as exc:
        raise ImportError(
            "shared data_lake runtime not found: install the [lake] extra "
            "(pip install -e .[lake]) or set SOLO_DATA_LAKE_ROOT to a "
            "data-lake checkout"
        ) from exc
    return pa


_pa = None


def _pa_mod():
    global _pa
    if _pa is None:
        _pa = _load_shared()
    return _pa


class LakeUnavailable(RuntimeError):
    pass


class LakeIngestError(RuntimeError):
    pass


def _sync_exc():
    pa = _pa_mod()
    global LakeUnavailable, LakeIngestError
    LakeUnavailable = pa.LakeUnavailable  # type: ignore
    LakeIngestError = pa.LakeIngestError  # type: ignore


def _contract():
    pa = _pa_mod()
    _sync_exc()
    return pa.LakeProductContract(
        source=config.LAKE_SOURCE,
        domain=config.LAKE_DOMAIN,
        product_schema_version=config.SCHEMA_VERSION,
        privacy_class=config.LAKE_PRIVACY_CLASS,
        retention_class=config.LAKE_RETENTION_CLASS,
        bronze_schema_version=config.LAKE_BRONZE_SCHEMA_VERSION,
        project_root=config.PROJECT_ROOT,
        data_lake_uri=config.DATA_LAKE_URI,
        lineage_filename=config.LINEAGE_FILE,
        datasets=(config.LAKE_DATASET_TOOLS, config.LAKE_DATASET_HISTORY),
    )


def utc_now_iso() -> str:
    return _pa_mod().utc_now_iso()


def shared_runtime_available() -> bool:
    """Return True when the shared ``data_lake`` runtime can be imported.

    Covers the installed ``[lake]`` extra (``solo-empire-data-lake``) and the
    ``SOLO_DATA_LAKE_ROOT`` sibling-checkout fallback.
    """
    if importlib.util.find_spec("data_lake") is not None:
        return True
    try:
        _pa_mod()
    except ImportError:
        return False
    return True


def default_data_lake_uri(solo_root: Optional[Path] = None) -> str:
    del solo_root
    return _pa_mod().default_data_lake_uri(
        data_lake_uri=config.DATA_LAKE_URI,
        project_root=config.PROJECT_ROOT,
    )


_UNSAFE_SCHEMES = (
    "javascript:",
    "data:",
    "vbscript:",
    "mailto:",
    "file:",
    "tel:",
    "ftp:",
)


def is_unsafe_url(url: str) -> bool:
    """True for script/data/file-style URLs that must never be stored as a tool URL."""
    return str(url or "").strip().lower().startswith(_UNSAFE_SCHEMES)


def canonical_url(url: str) -> str:
    """Stable tool identity from URL (scheme/host/path; drop query/fragment).

    Returns ``""`` for unsafe schemes (``javascript:``, ``data:`` ...).
    """
    raw = str(url or "").strip()
    if not raw or is_unsafe_url(raw):
        return ""
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    try:
        parsed = urlparse(raw)
    except Exception:  # noqa: BLE001
        return raw.rstrip("/").lower()
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = re.sub(r"/+", "/", (parsed.path or "/")).rstrip("/") or ""
    # Keep only scheme + host + path
    return urlunparse(("https", host, path, "", "", "")).rstrip("/")


def stable_tool_id(url: str) -> str:
    can = canonical_url(url)
    if not can:
        digest = hashlib.sha1(str(url).encode("utf-8")).hexdigest()[:16]
        return f"hash:{digest}"
    # Path-safe for /v1/records/{id}
    safe = can.replace("https://", "").replace("/", "_")
    return safe


def normalize_tool_record(
    tool: dict[str, Any],
    *,
    provider: str,
    observed_at: Optional[str] = None,
    source_page_url: str = "",
) -> dict[str, Any]:
    observed = observed_at or utc_now_iso()
    url = str(tool.get("url") or "").strip()
    can = canonical_url(url)
    if is_unsafe_url(url):
        # Script/data/file URLs are never stored as a clickable tool URL.
        url = ""
    upvotes = tool.get("upvotes", "")
    if upvotes not in ("", None) and quality.non_negative_number(upvotes) is None:
        upvotes = ""
    name = str(tool.get("name") or "").strip()
    vendor = str(tool.get("vendor") or tool.get("source") or provider or "").strip()
    category = str(tool.get("category") or "").strip()
    pricing = str(tool.get("pricing") or "").strip()
    description = str(tool.get("description") or "").strip()
    rid = stable_tool_id(url or can or name)
    return {
        "id": rid,
        "canonical_url": can or url,
        "url": url or can,
        "name": name,
        "vendor": vendor,
        "category": category,
        "pricing": pricing,
        "description": description[:500],
        "source": str(tool.get("source") or provider),
        "provider": provider,
        "upvotes": upvotes,
        "tags": tool.get("tags", ""),
        "opportunity_tags": tool.get("opportunity_tags", ""),
        "source_page_url": source_page_url,
        "observed_at": observed,
        "event_time": observed,
        "content_type_hint": str(tool.get("content_type_hint") or "text/html"),
    }


def tool_records_from_list(
    tools: list[dict[str, Any]],
    *,
    provider: str,
    observed_at: Optional[str] = None,
    source_page_url: str = "",
    dedupe: bool = True,
) -> list[dict[str, Any]]:
    observed = observed_at or utc_now_iso()
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        rec = normalize_tool_record(
            tool,
            provider=provider,
            observed_at=observed,
            source_page_url=source_page_url,
        )
        if not rec["canonical_url"] and not rec["name"]:
            continue
        rid = rec["id"]
        if dedupe:
            if rid not in by_id:
                order.append(rid)
            by_id[rid] = rec
        else:
            order.append(rid)
            by_id[f"{rid}#{len(order)}"] = rec
    if dedupe:
        return [by_id[i] for i in order]
    return [by_id[k] for k in order]


def ingest_provider_payload(
    *,
    provider: str,
    raw: bytes,
    tools: list[dict[str, Any]],
    dataset: str,
    content_type: str = "text/html",
    input_format: str = "html",
    data_lake_uri: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    source_page_url: str = "",
    observed_at: Optional[str] = None,
) -> dict[str, Any]:
    """Land one provider/page response; never merge raw bytes across pages."""
    _sync_exc()
    records = tool_records_from_list(
        tools,
        provider=provider,
        observed_at=observed_at,
        source_page_url=source_page_url,
        dedupe=True,
    )
    if not records:
        raise LakeIngestError(f"Provider {provider!r} produced zero tool records")

    fmt = input_format
    ctype = content_type
    if ctype.startswith("text/html") or fmt in {"html", "htm"}:
        fmt = "html"
        ctype = "text/html"

    meta = {
        "provider": provider,
        "capture_mode": "per_provider_url",
        "content_type": ctype,
        "source_page_url": source_page_url,
        "tool_count": len(records),
    }
    if metadata:
        meta.update(metadata)

    return _pa_mod().ingest_to_lake(
        _contract(),
        raw=raw,
        records=records,
        dataset=dataset,
        data_lake_uri=data_lake_uri,
        metadata=meta,
        content_type=ctype,
        input_format=fmt,
        provider=provider,
    )


def write_lineage(
    result: dict[str, Any],
    *,
    dataset: str,
    data_dir: Optional[Path] = None,
    provider: str = "",
    page_key: str = "",
) -> Path:
    key = dataset
    if provider:
        key = f"{dataset}::{provider}"
    if page_key:
        key = f"{key}::{page_key[:48]}"
    return _pa_mod().write_lineage(
        _contract(),
        result,
        dataset=key,
        data_dir=Path(data_dir or config.DATA_DIR),
    )


def load_lineage(data_dir: Optional[Path] = None) -> Optional[dict[str, Any]]:
    return _pa_mod().load_lineage(
        Path(data_dir or config.DATA_DIR),
        filename=config.LINEAGE_FILE,
    )


def bronze_dataset_dir(
    dataset: str,
    *,
    data_lake_uri: Optional[str] = None,
) -> Path:
    return _pa_mod().bronze_dataset_dir(
        _contract(), dataset, data_lake_uri=data_lake_uri
    )


def read_bronze_rows(
    dataset: str,
    *,
    data_lake_uri: Optional[str] = None,
    sql: str | None = None,
) -> list[dict[str, Any]]:
    _sync_exc()
    return _pa_mod().read_bronze_rows(
        _contract(), dataset, data_lake_uri=data_lake_uri, sql=sql
    )


def read_iceberg_rows(
    dataset: str,
    *,
    data_lake_uri: Optional[str] = None,
    sql: str | None = None,
) -> list[dict[str, Any]]:
    _sync_exc()
    return _pa_mod().read_iceberg_rows(
        _contract(), dataset, data_lake_uri=data_lake_uri, sql=sql
    )


def parse_payload_json(row: dict[str, Any]) -> dict[str, Any]:
    return _pa_mod().parse_payload_json(row)


def select_latest_bronze_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _pa_mod().select_latest_bronze_rows(rows)


def landing_object_bytes(raw_key: str, *, data_lake_uri: Optional[str] = None) -> bytes:
    _sync_exc()
    uri = data_lake_uri or default_data_lake_uri()
    return _pa_mod().landing_object_bytes(uri, raw_key)
