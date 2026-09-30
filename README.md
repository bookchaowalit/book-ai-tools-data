# book-ai-tools-data

AI tools intelligence — **lake-first**, public directories/pages only by default.

Migration status: shared `data_lake.product_adapter`. Each directory page is
landed separately (provider + URL). HTML exact bytes go to landing; Bronze holds
structured tool fields + `raw_object_key`.

Collection cron: `bash setup_cron.sh install` runs Futurepedia HTML capture daily at 11:00 into `data/exported/`. Lake ingest remains a separate command.

## Data contract

| Field | Value |
|---|---|
| `source` | `book-ai-tools-data` |
| `domain` | `ai-tools` |
| `datasets` | `ai_tools`, `ai_tools_history` |
| Bronze schema | `1` |
| envelope | `ai_tools.v1` |
| port | `8107` |

```text
Public directory/page bytes (per URL)
    → landing/source=book-ai-tools-data/provider=public_directories/…
    → bronze/domain=ai-tools/dataset=ai_tools|ai_tools_history
    → manifest + lineage
    → DuckDB API
    → optional CSV projection
```

### Stable record identity

Canonical URL (scheme+host+path, no query/fragment) → `canonical_url` / `id`

Fields: `name`, `vendor`, `category`, `pricing`, `url`, `observed_at`

### Provider policy

| Provider | Default | Notes |
|---|---|---|
| `public_directories` | free | ProductHunt / TAAFT direct pages |
| `offline_fixtures` | free | Fixture / dry-run |
| `firecrawl` | **blocked** | Before network |
| `brave_search_api` | **blocked** | Before network |
| `paid_search` | **blocked** | Before network |

## Quick start

```bash
pip install -e .
# Lake writes need the shared data-lake runtime: the [lake] extra installs the
# pinned solo-empire-data-lake (plus pyarrow/duckdb); inside Solo Empire the
# parent checkout's infra/scripts/data_lake is used instead
pip install -e ".[lake]"

python -m book_ai_tools.ingest --fixture
python -m book_ai_tools.ingest --sources producthunt,taaft \
  --data-lake-uri /path/to/data/lake

python -m book_ai_tools.api --host 127.0.0.1 --port 8107
```

Lake failure exits non-zero and does **not** update `ai_tools.csv`.
`--sources` accepts `producthunt`, `taaft` (aliases `taft`,
`theresanaiforthat`) and `search`; unknown sources or a negative
`--min-upvotes` exit with status 2.

## Optional Iceberg REST catalog read pilot

AI tools uses the shared `product_store` feature flag. Direct Bronze Parquet
remains the default:

| Variable | Default | Meaning |
|---|---|---|
| `LAKE_READ_MODE` | `parquet` | Direct Bronze read; `iceberg` resolves the registered catalog table and queries it with DuckDB |
| `LAKE_READ_FALLBACK` | `error` | Fail-closed by default; `parquet` is an explicit fallback |
| `ICEBERG_CATALOG_URI` | unset | REST/SQL catalog URI from the runtime secret provider |
| `ICEBERG_WAREHOUSE_URI` | unset | Provider-issued warehouse URI/name |
| `ICEBERG_CATALOG_TOKEN` | unset | Catalog credential; never store it in the repository or API response |

The matching tables must be registered before enabling Iceberg mode:
`bronze.ai-tools_ai_tools` and `bronze.ai-tools_ai_tools_history`.

```bash
npx --yes @infisical/cli@0.43.120 run \
  --projectId=<solo-empire-project-id> --env=dev --path=/cloudflare -- \
  bash -lc 'export SOLO_EMPIRE_DATA_LAKE_URI=s3://<bucket>/<prefix>; \
    export LAKE_READ_MODE=iceberg; export LAKE_READ_FALLBACK=error; \
    PYTHONPATH=src /path/to/solo-empire/.venv/bin/python \
    -m book_ai_tools.api --host 127.0.0.1 --port 8107'
```

Successful `/v1/metadata` reports
`storage_model=lake_first_iceberg_rest_duckdb` and both source kinds as
`iceberg_rest_duckdb`. CSV remains CLI-only and is not a fallback unless the
explicit `LAKE_READ_FALLBACK=parquet` flag is selected.

### Live R2 parity evidence

Verified on 2026-08-08 with bounded prefix
`portfolio-demo/book-ai-tools-iceberg-001`:

| Check | Result |
|---|---|
| Tables | `bronze.ai-tools_ai_tools`, `bronze.ai-tools_ai_tools_history` |
| Providers | `offline_fixtures`, `public_directories` |
| Rows | 2 tools + 2 history |
| Iceberg vs direct Parquet | semantic parity passed for both datasets |
| Per-provider/URL landing | passed; two Bronze files per dataset |
| HTML exact bytes and canonical URL lineage | exact / passed |
| Registration retry | `already_registered` for all files |
| Snapshot retry | idempotent for all files |
| API storage model | `lake_first_iceberg_rest_duckdb` on port 8107 |
| CSV dependency | none |

The check used deterministic public-directory-shaped fixtures and did not
contact Firecrawl, Brave, or paid search. The old observation timestamp makes
`data_status=stale` expected; the default `LAKE_READ_MODE=parquet` remains
unchanged.

## API

| Method | Path | Notes |
|---|---|---|
| GET | `/v1/metadata` | `storage_model=lake_first_bronze_duckdb` (Parquet) or `lake_first_iceberg_rest_duckdb` (Iceberg flag) |
| GET | `/v1/records` | Bronze latest (not CSV) |
| GET | `/v1/history` | Bronze history |
| POST | `/v1/refresh` | 403 by default |

## Data quality

- Tool URLs with `javascript:`, `data:`, `vbscript:`, `mailto:`, `file:`,
  `tel:` or `ftp:` schemes are never stored (`url`/`canonical_url` blank; a
  record with no name either is dropped). Ids for normal URLs are unchanged.
- `upvotes` must be a non-negative number; anything else is blanked.
- NEW-tool alerts compare against the history *before* the current run.
- Package CSV projections and the Futurepedia collector's snapshot/history/raw
  files are written atomically (temp file + `os.replace`).
- The Futurepedia parser resolves relative `/tool/<slug>` links and is tested
  offline against `tests/pages/futurepedia_page.html`.

## Tests

```bash
# What CI runs: the [lake] extra installs the pinned solo-empire-data-lake
# runtime (plus pyarrow/duckdb), so Bronze/Silver lake tests run standalone;
# requirements-collect.txt adds bs4 for the offline Futurepedia parser tests
python -m pip install -e ".[lake]" -r requirements-collect.txt pytest ruff
ruff check .
python -m pytest -q -rs

# Contract/policy only: without the extra, lake tests skip with a reason
python -m pip install -e . pytest
python -m pytest -q -rs

# Inside Solo Empire: SOLO_EMPIRE_ROOT (or walking parents) makes the parent
# checkout's infra/scripts/data_lake take precedence over the installed runtime
SOLO_EMPIRE_ROOT=/path/to/solo-empire python -m pytest -q
```
