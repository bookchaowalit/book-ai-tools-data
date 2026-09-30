# Upgrade plan — book-ai-tools-data

Score: 8/10 -> 8.5/10 — Futurepedia parsing is covered by a saved HTML fixture, NEW-tool alerts work again, unsafe URLs/bad upvotes are rejected and projections are atomic; remaining gaps are packaging polish.

## Backlog

- P0: Confirm the first CI run on GitHub is green (it now downloads the pinned
  `solo-empire-data-lake` tarball); keep it required on `main`.
- P1: When `solo-empire-data-lake` moves, bump the pinned commit in `[lake]` together with
  the other book-*-data repos (same SHA everywhere).
- P2: Add a `[project.optional-dependencies] dev` extra and a `[build-system]` table so
  `pip install -e ".[dev]"` is the single documented setup.
- P1: Add saved-page fixtures for `parse_producthunt` / `parse_taft` (markdown inputs) like the
  Futurepedia one, including the multi-line link joining in `parse_producthunt`.
- P2: Move the Futurepedia collector's CSV writers onto `book_ai_tools.fsutil` once the
  script can rely on the package being installed.

## Done in this pass (pass 4: edge cases)

- `config.env_bool` returned False for anything but a true-ish word, so
  `FREE_ONLY=` (blank line in .env/compose) or a typo silently disabled the
  free-only guard; blank/unrecognised values now keep the safe default
  (`EnvBoolTests` in `tests/test_edge_cases.py`).
- `quality.finite_number` raised `OverflowError` for a JSON integer beyond
  float range (`1` followed by 400 zeros); it now returns `None`.
- `--min-upvotes` filter crashed the whole run with `ValueError`: it gated
  `float()` on `str.isdigit()`, which is also true for `²`/`①`; it now uses
  `quality.non_negative_number`.
- `scripts/scrape_ai_tools._clean_text` drops U+200B/U+2060/BOM/soft hyphen,
  so a tool name made only of invisible characters is skipped as blank.
- Verified: `tests/test_edge_cases.py` (all 3 fail on the old code); full
  suite 48 passed; ruff 0.15.8 + 0.16.9.

## Done in this pass (pass 3)

- Offline Futurepedia parser test (`tests/test_futurepedia_parser.py` + saved HTML page):
  canonical/duplicate/off-site/non-HTTPS/orphan cards, rating bounds, unsafe `Visit` links,
  pricing/categories/bookmarks. The parser now resolves relative `/tool/<slug>` hrefs.
- Fixed NEW-tool detection: previous URLs were loaded *after* this run appended to history,
  so every tool looked seen and the alert never fired.
- `lake.normalize_tool_record` blanks unsafe URL schemes and invalid upvotes (new `quality` module).
- New `fsutil` module for atomic package projections; the collector script writes
  snapshot/history/raw atomically too.
- CLI: `--sources` validated with `taft`/`theresanaiforthat` aliases (help text named `taft`,
  which was silently ignored); `--min-upvotes` must be >= 0. `utcnow()` replaced.
- CI installs `requirements-collect.txt` so the parser tests run; README updated.
- Verified: `pytest -q -rs` 42 passed, 0 skipped (was 28) with the `[lake]` venv; ruff
  0.15.8 and 0.16.9 clean.
- Refresh auth: `/v1/refresh` compares the bearer token with `hmac.compare_digest`
  (`_refresh_token_ok`) instead of `==`, which leaked the matching prefix
  length through timing; `tests/test_refresh_token_compare.py` pins it.
- CSV projection stamps (`_projection_timestamp`) are UTC; they were host-local
  but `product_store.parse_ts` reads naive stamps as UTC, so freshness was off
  by the host offset (`tests/test_projection_timestamp_utc.py`).

## Done in pass 2

- Added a `[lake]` extra pinning `solo-empire-data-lake` at `68fb5a9` (plus pyarrow/duckdb); CI
  installs `-e ".[lake]"`, asserts `data_lake` imports, and lake tests now run instead of skipping.
- Test guards use `lake.shared_runtime_available()` (`importlib.util.find_spec("data_lake")`,
  then the `SOLO_EMPIRE_ROOT` / parent-checkout fallback) instead of `find_solo_empire_root()`.
- `[tool.ruff.lint] select = ["E4", "E7", "E9", "F"]` pins the classic rule set: unpinned
  ruff 0.16 widened its defaults and would have failed `ruff check .` in CI.
- `tests/test_fixture_headers.py` pins `fixtures/*.csv` headers to the CSV projection;
  the fixtures had drifted (missing `canonical_url`, `vendor`, `provider`, `observed_at`) and were refreshed.
- Verified: fresh venv `pip install -e ".[lake]"` from the pinned tarball — 0 skipped of 28 with `[lake]` (was 10 of 27 skipped);
  also against `pip install /home/user/solo-empire-data-lake`, with `SOLO_EMPIRE_ROOT=<parent>`
  (parent adapter wins), and without the extra (lake tests skip with a reason). ruff 0.15 and 0.16 clean.

## Done in pass 1

- `lake.find_solo_empire_root()` now returns `None` when the shared `data_lake`
  adapter cannot be imported (it raised `ModuleNotFoundError` before), and the
  adapter loader also honours `SOLO_EMPIRE_ROOT` so a sibling clone of the parent
  repo works without editing `PYTHONPATH`.
- CI: installs `pytest ruff`, runs `ruff check .` and `python -m pytest -q -rs`
  (skip reasons visible) instead of bare `unittest`.
- Fixed the remaining default-rule ruff findings; `.gitignore` covers egg-info/ruff cache.
- README "Tests" section documents the standalone and full-lake commands.
- Verified: clean venv with `pip install -e . pytest` (CI shape) and a full run with
  `SOLO_EMPIRE_ROOT=<parent>` + pyarrow/duckdb (all lake tests execute and pass).
- The fail-closed ingest test now passes an explicit temporary `data_lake_uri`, so it runs
  standalone instead of touching the default lake path.
