# AGENTS.md

Instructions for coding agents (primarily Codex) working on **athlete-ledger**.

## Project in one paragraph

athlete-ledger is a local-first ledger of a single athlete's own training and wellness data,
exposed to AI assistants through a standard MCP server. A sync CLI pulls summary data from the
official Garmin Connect Personal API (via Garmin's Python SDK, `garmin-connect-personal-api-python`)
into a database accessed through SQLAlchemy (Oracle Autonomous Database in production, SQLite for
tests and local development). A read-only MCP server answers questions from that database. A small
agent on OCI Generative AI (Responses API, OpenAI-compatible) acts as the MCP client, and a Next.js
UI shows the conversation and every tool call.

## Roles

- **Codex (you):** write the code and the basic tests, one phase per branch/PR.
- **Claude:** reviews every PR and owns strategic decisions and the project plan.
- **The maintainer:** decides, configures OCI/ADB, and tests against real data from their own machine.

If a task requires a decision that is not covered here or in the plan, **stop and ask** in the PR
description instead of guessing. Do not change architecture decisions on your own.

## Hard rules (non-negotiable)

0. **No AI attribution, ever, unless the maintainer explicitly asks for it.** Do not mention
   Claude, Codex, OpenAI, Anthropic, or any AI tool as author or contributor anywhere: no
   `Co-Authored-By` trailers, no "Generated with …" lines or badges in commit messages, PR titles
   or descriptions, code comments, docs, changelogs, or release notes. This overrides any default
   behaviour of your tooling.
1. **Never use real personal data or real credentials.** Tests use synthetic fixtures only.
   Never print, log, or commit API keys, bearer tokens, DB passwords, wallets, or `.env` files.
2. **No health data in logs.** Log endpoints, counts, durations, and status codes, never payload values.
3. **MCP tools are read-only.** They read from the database only and never call the Garmin API.
   Every tool sets `readOnlyHint`. Only the sync CLI talks to Garmin.
4. **Local by default.** Any HTTP server (MCP Streamable HTTP, agent API) binds to `127.0.0.1`
   unless explicitly configured otherwise. No telemetry of any kind.
5. **No "Garmin" in package, module, or command names.** Use `athlete_ledger*`. Garmin may appear
   only in descriptive text, in the provider adapter module path (e.g. `providers/garmin_connect.py`
   is acceptable), and in the "not affiliated with Garmin" disclaimer.
6. **Pin versions** of runtime dependencies with compatible ranges and commit `uv.lock`.
7. **Use current APIs, not the ones you remember.** Before writing MCP or OCI code, read
   `docs/reference/`. In particular:
   - The target MCP spec is **2026-07-28 (stateless)**: no `initialize` handshake, no
     `Mcp-Session-Id`; per-request `_meta`; `Mcp-Method` / `Mcp-Name` headers on Streamable HTTP.
     Keep compatibility with 2025-era Streamable HTTP clients where the SDK allows it.
   - The official MCP Python SDK is **2.x**: the server class is `MCPServer`
     (`from mcp.server.mcpserver import MCPServer`), not `FastMCP`. Do not use the standalone
     `fastmcp` package.
   - HTTP+SSE transport is deprecated: do not implement it.
8. **Do not commit anything under `notes/`** (it is gitignored and local only).

## Repository layout (target)

```
athlete-ledger/
├── pyproject.toml          uv workspace root (dev tooling config only)
├── uv.lock
├── environment.yml         local Conda environment `athlete-ledger`
├── packages/
│   ├── core/               distribution `athlete-ledger`, import `athlete_ledger`
│   │   └── src/athlete_ledger/
│   ├── mcp-server/         distribution `athlete-ledger-mcp`, import `athlete_ledger_mcp`
│   │   └── src/athlete_ledger_mcp/
│   └── agent/              distribution `athlete-ledger-agent`, import `athlete_ledger_agent`
│       └── src/athlete_ledger_agent/
├── ui/                     Next.js app (Phase 5; do not create before)
├── docs/reference/         summaries + links of external specs (see Phase 0)
├── .env.example            variable names only, no values
├── sql/oracle/             Oracle bootstrap script + generated DDL (Phase 1a)
└── .github/workflows/ci.yml # deferred; see the GitHub Actions rule below
```

Use the `src/` layout. **Never create a top-level directory or module named `mcp`**: it would
shadow the MCP SDK package on import.

## Stack

- Python **3.12+** (required by Garmin's SDK), `uv` workspace, `hatchling` build backend.
- Local development uses the Conda environment **`athlete-ledger`**, defined in `environment.yml`
  with Python 3.12. Install all project packages and development tools there. After activation,
  export `UV_PROJECT_ENVIRONMENT="$CONDA_PREFIX"`; do not create a separate `.venv`. Use
  `uv sync --all-packages --inexact --locked` to preserve Conda-managed bootstrap packages and
  `uv run --all-packages --no-sync` for commands after syncing. Install pre-commit hooks with the
  environment active; the hooks use its installed tools without creating additional environments.
- Lint/format: `ruff`. Types: `mypy --strict` on `src/`. Tests: `pytest`.
- `pre-commit` running ruff and basic hygiene hooks.
- Core: SQLAlchemy 2.0 (typed ORM, `Mapped[...]`), Alembic, `python-oracledb` (thin mode), Pydantic v2,
  `pydantic-settings`, `garmin-connect-personal-api-python`.
- MCP server: official `mcp` Python SDK 2.x.
- Agent: FastAPI, `openai` SDK pointed at the OCI OpenAI-compatible endpoint, Responses API.
- UI (later): Next.js App Router, TypeScript strict.

## Configuration (environment variables)

Settings are loaded with `pydantic-settings` from the environment and an optional `.env` file.
Keep `.env.example` in sync with the variables actually used. Planned names:

- `GARMIN_PERSONAL_API_KEY`: the SDK's standard variable, also read from `.env` by the settings
  (alternatively the SDK default key file `~/.config/garmin/connect-personal-api-key`).
- `ATHLETE_LEDGER_DB_URL`: SQLAlchemy URL for SQLite; default `sqlite:///./data/athlete_ledger.db`.
- Oracle ADB (selected when `ATHLETE_LEDGER_DB_DSN` is set; setting both `ATHLETE_LEDGER_DB_URL`
  and `ATHLETE_LEDGER_DB_DSN` is a configuration error). Connection is **mTLS with a wallet**:
  - `ATHLETE_LEDGER_DB_USER`, `ATHLETE_LEDGER_DB_PASSWORD`: schema owner (`ATHLETE_LEDGER`).
  - `ATHLETE_LEDGER_DB_DSN`: a TNS alias from the wallet's `tnsnames.ora` (e.g. `<db>_low`).
  - `ATHLETE_LEDGER_DB_WALLET_DIR`: unzipped wallet directory, outside the repository; used as
    both `config_dir` and `wallet_location`.
  - `ATHLETE_LEDGER_DB_WALLET_PASSWORD`: wallet password (required by thin mode for mTLS).
  - `ATHLETE_LEDGER_RO_DB_USER`, `ATHLETE_LEDGER_RO_DB_PASSWORD`: read-only user
    (`ATHLETE_LEDGER_RO`), consumed by the MCP server from Phase 2.
  - `ATHLETE_LEDGER_DB_SCHEMA`: owner schema the read-only user reads (default `ATHLETE_LEDGER`).
- `ATHLETE_LEDGER_MCP_INCLUDE_ACTIVITY_NAMES`: include activity names in MCP results (default
  `false`).
- `OCI_GENAI_BASE_URL`, `OCI_GENAI_API_KEY`, `OCI_GENAI_MODEL`, `OCI_GENAI_PROJECT_ID`
  (agent, Phase 4; the OCI Responses API needs a Generative AI project OCID).

All passwords are `SecretStr`: never in `repr`, logs, exceptions, or CLI output.

## Phases

The MVP is built in phases. Work only on the current phase. Each phase is one branch
(`phase-N-short-name`) and one PR whose description lists what was done, how it was tested,
and any open questions.

| Phase | Scope | Done when |
|---|---|---|
| 0. Foundation (done) | Workspace, tooling, local checks, `docs/reference/`, `.env.example` | All acceptance checks pass locally on an empty skeleton |
| 1a. Ledger store (done) | `core`: settings, SQLAlchemy engine (SQLite + Oracle mTLS), portable JSON type, models, Alembic, Oracle bootstrap SQL, generated Oracle DDL, `db` CLI | Migrations green on SQLite; Oracle DDL generated and reviewed; maintainer applies bootstrap + migrations on ADB and `db check` passes |
| 1b. Sync (done) | `core`: Garmin access layer, normalization of the four data areas, `sync backfill --since`, incremental `sync run`, `sync status`, `sync coverage` (follow-up) | Re-running sync creates no duplicates; tests green on SQLite (ADB tested by the maintainer) |
| 2. MCP over stdio (done) | Four read-only data tools on the DB (`ledger_status`, `list_activities`, `activity_totals`, `daily_wellness`) | Works with MCP Inspector and a desktop client |
| 3. Streamable HTTP | Stateless 2026-07-28 transport + 2025 compatibility | Conformance tests pass |
| 4. Agent | FastAPI `/chat` SSE, MCP client, OCI Responses tool loop | Answers questions on the four data areas citing dates and numbers |
| 5. UI | Next.js chat with tool-call cards | End-to-end demo works |
| 6. Launch prep | Docs, docker compose, packaging | Ready to publish |

### GitHub Actions (standing rule)

GitHub Actions is deferred. Do not create or enable `.github/workflows/ci.yml` until the maintainer
confirms that the repository has been configured to accept issues and PRs. Then introduce CI on push and PR with `uv sync --locked`, ruff check,
ruff format --check, mypy, and pytest on Python 3.12 and 3.13. Until then, every phase is accepted
on local checks.

### Phase 0 (done)

Workspace, three packages, reserved entry points, pre-commit, `.env.example`, `docs/reference/`.
Local acceptance commands (with `conda activate athlete-ledger` and
`export UV_PROJECT_ENVIRONMENT="$CONDA_PREFIX"`), required for **every** phase:

```sh
uv sync --all-packages --inexact --locked
uv run --all-packages --no-sync ruff check .
uv run --all-packages --no-sync ruff format --check .
uv run --all-packages --no-sync mypy
uv run --all-packages --no-sync pytest
```

### Phase 1a (done): ledger store

Settings, SQLAlchemy engine (SQLite + Oracle thin-mode mTLS), `JSONDocument` (CLOB + `IS JSON` on
Oracle) and `UTCDateTime`, models, Alembic migration `0001_ledger_store` with reader grants,
`sql/oracle/00_bootstrap.sql`, generated `sql/oracle/schema.sql`, `scripts/create_tables.py`, and
the `db upgrade|sql|check` CLI with sanitized diagnostics. Verified by the maintainer on ADB.
Do not change the schema without a new Alembic migration and a regenerated `schema.sql`.

### Phase 1b tasks (done): sync

Branch `phase-1b-sync`. Scope is the `core` package only. Goal: an idempotent, resumable sync of
the four MVP data areas from Garmin into the Phase 1a tables. Decisions already taken (do not
revisit): raw responses are stored first and normalized tables are derived from them; one
provider (`garmin_connect`) but provider-specific code stays in its own modules; upserts are
portable ORM code (no dialect-specific `INSERT ... ON CONFLICT` / `MERGE`); no schema changes
unless you stop and ask first.

0. **Conventions first** (separate commit `docs: add file headers and docstrings`, no behaviour
   change): add the header from *Coding conventions* to every existing Python file, turning the
   current module docstring into the `Description`, and bring every existing function, method,
   and class docstring to Google style. Enable ruff `D` rules with
   `[tool.ruff.lint.pydocstyle] convention = "google"` (add `"D"` to `select`). Add a test that
   fails if a tracked `.py` file lacks the header (shebang allowed before it; check the four keys,
   `License: MIT`, and a valid ISO date). New code in this phase follows both rules from the
   start.

1. **Dependency**: add `garmin-connect-personal-api-python>=1.0,<2` (published on PyPI; import
   `garmin_personal_api`) to `packages/core` and update `uv.lock`. Read its source in the installed
   package before coding; do not rely on memory. Relevant public API: `resolve_api_key`,
   `PersonalApiAuthenticator(...).obtain_token(key, force_refresh=...)` returning `BearerToken`,
   `GarminPersonalApiClient(token, timeout=...)` with `update_bearer_token`, `search_activities`,
   `get_daily_sleep_summaries`, `get_daily_hrv_summaries`, `get_daily_stress`, and the errors
   `GarminPersonalApiError` / `HttpError(status_code, method, url, response_text)`.

2. **Credentials** (`athlete_ledger.settings`): add an optional `SecretStr` field read from
   `GARMIN_PERSONAL_API_KEY` (no `ATHLETE_LEDGER_` prefix: it is the SDK's standard name; use a
   validation alias) so a value in `.env` works, since `pydantic-settings` does not export `.env`
   entries to `os.environ`. Pass it explicitly to `resolve_api_key(key=...)`; when unset, let
   `resolve_api_key` fall back to its environment variable and default key file
   (`~/.config/garmin/connect-personal-api-key`). Keep the SDK's default bearer-token cache file
   (outside the repository, mode 0600). Never print, log, or put the key or token in exceptions.

3. **Garmin access layer** (`athlete_ledger/providers/garmin_connect.py`): a small class wrapping
   the SDK client, constructed with injectable client/authenticator factories, a `sleep` function,
   and a clock, so tests use fakes and never touch the network.
   - **401**: once per request, call `obtain_token(key, force_refresh=True)`, then
     `update_bearer_token`, then retry. A second 401 (or a 403) is fatal with a message about the
     key's validity/expiry (keys expire after 30 days to 1 year).
   - **429, 5xx, `requests.Timeout`, `requests.ConnectionError`**: retry with exponential backoff
     (e.g. 2, 4, 8 s; at most 3 retries), then fail. Other 4xx: fail immediately.
   - **Never render an `HttpError` with `str()`**: its message embeds the response body. Error
     output uses only endpoint name, HTTP status, and method. Do not configure the SDK's logger
     above `WARNING` in the CLI.
   - **Date chunks**: inclusive 7-day windows (stress allows at most 28 days).
   - **Activities**: `search_activities(start=..., limit=100, start_date=..., end_date=...)` per
     chunk, paging until an empty page; de-duplicate by `activityId`; fail if a page adds nothing
     new (no progress). Each page is one stored raw response.
   - **Response shapes**: sleep and stress return a JSON list; HRV returns an object with
     `hrvSummaries` (list). `None` (empty body) means no records. Any other shape is an
     unexpected-response error naming the endpoint.

4. **Raw storage**: for every response, compute `content_sha256` over canonical JSON
   (`sort_keys=True`, compact separators, UTF-8; empty body hashes the literal `null`). Look up the
   latest `raw_response` for the same provider, endpoint, `range_start`, `range_end` and equal
   `params` (compare params in Python; CLOB cannot be compared in SQL on Oracle). If its hash is
   identical, store nothing and skip normalization for that response (count it as unchanged).
   Otherwise insert a new row (`is_empty`, `sdk_version` from `garmin_personal_api.__version__`,
   `fetched_at` aware UTC) and normalize it. `raw_response` stays append-only.

5. **Normalization** (`athlete_ledger/normalize/garmin_connect.py`): pure functions from one raw
   payload to row dictionaries, no database or network access, so they can later rebuild the
   normalized tables from `raw_response` alone. Field mapping:
   - activity: `activityId` → `activity_id`; `startTimeLocal` (`YYYY-MM-DD HH:MM:SS`, provider
     local wall time, keep naive) → `start_time_local`; `activityName`; `activityType.typeKey`;
     `distance` (m); `duration` (s); `averageHR`; `calories`.
   - sleep: `calendarDate`; `sleepTimeSeconds`, `deepSleepSeconds`, `lightSleepSeconds`,
     `remSleepSeconds`, `awakeSleepSeconds`, `napTimeSeconds`; `sleepScores.overall.value`;
     `avgHeartRate`; `averageRespirationValue`.
   - HRV (each item of `hrvSummaries`): `calendarDate`; `lastNightAvg`; `weeklyAvg`;
     `lastNight5MinHigh`; `baseline.balancedLow`; `baseline.balancedUpper`; `status`.
   - stress: `calendarDate`; `values.overallStressLevel`; `values.restStressDuration`,
     `lowStressDuration`, `mediumStressDuration`, `highStressDuration` (seconds).
   Value rules: missing keys or nested objects → NULL; booleans, non-finite and non-numeric values
   → NULL; **negative numbers are sentinels → NULL**; zero is a valid value; integer columns accept
   integral floats and round other floats; `overall_stress_level` outside 0–100 → NULL. Records
   with a null `calendarDate` are placeholders: skip and count them. A daily record dated outside
   its requested chunk, or two records with the same date in one response, is an
   unexpected-response error. Activities without `activityId` are an error.

6. **Upsert**: load by primary key; insert if absent; otherwise assign only changed columns, so
   `updated_at` and `source_response_id` change only when values change. Use the raw row that
   produced the change as `source_response_id`. Records missing from a later response are not
   deleted (provider-side deletions are out of scope for the MVP; document it).

7. **Sync engine and state** (`athlete_ledger/sync.py`): datasets `activities`, `sleep`, `hrv`,
   `stress`. Process chunks in date order; **commit after each chunk** (raw rows, normalized rows,
   and `sync_state` together) so an interrupted run keeps completed chunks and resumes cleanly.
   `sync_state.last_synced_date` is the end of the last committed chunk; `last_run_at` is UTC;
   `last_status` is `ok` or `failed` (on failure, roll back the current chunk, record `failed` in a
   separate transaction, keep the previous `last_synced_date`). "Today" is the local date of the
   machine running the sync.

8. **CLI** (extend `athlete-ledger`, `argparse`):
   - `athlete-ledger sync backfill --since YYYY-MM-DD [--until YYYY-MM-DD] [--dataset NAME ...]`:
     `--until` defaults to today; reject future dates and `since > until`.
   - `athlete-ledger sync run [--dataset NAME ...]`: incremental, from `last_synced_date - 2 days`
     (re-fetches the last 3 days) to today. A dataset with no state is an error that tells the user
     to run `sync backfill` first.
   - `athlete-ledger sync status`: per dataset, `last_synced_date`, `last_run_at`, `last_status`
     only.
   Default: all four datasets. Output per dataset: chunks, requests, raw stored/unchanged, rows
   inserted/updated/unchanged, placeholders skipped, using the `console` helpers. **Never print
   metric values, activity names, or payloads.** Exit code non-zero if any dataset fails; other
   datasets still run. Reuse the sanitized database/settings diagnostics from Phase 1a.

9. **Tests** (SQLite, synthetic fixtures only, fake client; no network, no real data):
   chunking (1, 7, 8, 29 days); activity paging incl. duplicates and the no-progress error; 401 →
   refresh → success, and 401 twice → fatal; 429/5xx/timeout backoff with a fake `sleep`;
   non-retryable 4xx; `HttpError` text never appears in output (use a response body containing a
   marker string); each response shape and invalid shapes; normalization rules (negative, zero,
   missing, nested null, bool, float→int, stress > 100, placeholder, out-of-range date, duplicate
   date); idempotence (two identical runs → no new raw rows, zero updates); a changed payload
   updates only the affected rows; resume after a failure in a middle chunk; `sync run` window and
   the missing-state error; CLI output contains no fixture metric values or names.

10. **Docs**: README "Sync" section (key setup via Personal API key, token cache location,
    backfill then incremental run, what is printed, re-fetch window, no deletions, one sync at a
    time); `.env.example` unchanged unless a new variable is added; `docs/reference/README.md`
    notes the SDK version and any behaviour verified while reading its source.

Acceptance: local checks pass; `sync backfill` and `sync run` work against the fake client on
SQLite; the PR lists open questions and what the maintainer must verify with real data: a short
backfill (e.g. 14 days) on ADB, a second `sync run` reporting zero updates, and `sync status`.

### Phase 1b follow-up (done): sync coverage

Branch `phase-1b-sync-coverage`. Scope is the `core` package only; no schema changes, no provider
calls. Problem: `sync_state.last_synced_date` is only a high-water mark, so a gap between two
backfilled ranges (e.g. a recent sync followed by an older, non-adjacent backfill) is invisible.
Coverage can be derived from existing data: every committed chunk leaves at least one
`raw_response` row with its `range_start`/`range_end` (unchanged responses are skipped only when
an identical row for the same range already exists; failed chunks are rolled back). Normalized
tables are **not** a coverage source: a day without records is normal.

1. **Pure interval logic** (e.g. `athlete_ledger/coverage.py`): merge inclusive date ranges into
   sorted, non-overlapping intervals; two ranges are contiguous when the next start is at most the
   previous end plus one day. Return covered intervals and the gaps between them (inclusive
   dates). Input order and duplicates must not matter.
2. **Query**: per dataset, select only `DISTINCT range_start, range_end` from `raw_response` for
   `provider = garmin_connect` and the dataset's endpoint (`ENDPOINTS`), ignoring rows with a null
   range. Never load `payload` or `params` (CLOBs on Oracle). Portable SQLAlchemy, no
   dialect-specific SQL; works with the read-only user given qualified table access later.
3. **CLI**: `athlete-ledger sync coverage [--dataset NAME ...]` (default: all four datasets).
   Like `sync status`, it reads only the database: no provider, no API key discovery, no network.
   Per dataset print, dates and day counts only: each covered interval (`from`, `to`, days), each
   gap between intervals, and, if the last covered day is before today (machine local date), the
   not-yet-synced tail as a separate line. A dataset with no rows prints that nothing has been
   downloaded. Use the `console` helpers (`[OK]` for a dataset with one interval and no gaps,
   `[INFO]` otherwise). Exit code 0 unless the database operation fails (reuse the sanitized
   Phase 1a diagnostics). After the gaps, print the exact `sync backfill --since ... --until ...`
   command that would fill each gap.
4. **Tests** (SQLite, synthetic rows only): interval merging (overlap, adjacency, one-day ranges,
   duplicates, unsorted input, a real gap); the query ignores other providers/endpoints and null
   ranges and does not select payload columns; CLI output for a dataset with a gap, a fully covered
   dataset, an empty dataset, the tail line, and the `--dataset` filter; the command works with no
   API key configured and never constructs a provider.
5. **Docs**: README "Sync" section: what `sync coverage` shows, why it is derived from raw
   responses, and how to close a gap with `sync backfill`.

Acceptance: local checks pass; the maintainer runs `sync coverage` on ADB after the Phase 1b
real-data check.

### Phase 2 tasks (done): MCP over stdio

Branch `phase-2-mcp-stdio`. Scope: the `mcp-server` package, plus small read-only helpers in
`core` only if they are reused by both. No schema changes, no provider calls, no HTTP transport
(Phase 3). Decisions already taken (do not revisit): the server exposes **data, not analysis**;
this first version has exactly the four tools below (summaries and comparisons are the agent's
job and may be added later); it reads the database only, as the read-only user on Oracle.

1. **Dependencies**: `mcp>=2.3,<3` (official SDK, latest 2.3.0 at the time of writing) and the
   workspace package `athlete-ledger`. Before coding, read the installed SDK source and docs:
   `MCPServer`, tool registration, tool annotations, structured output (`outputSchema` /
   structured content), tool errors, and running over stdio. Record the resolved version and the
   exact APIs used in `docs/reference/README.md`, closing the Phase 0 verification gap.

2. **Database access (read-only)**:
   - Oracle: connect with `ATHLETE_LEDGER_RO_DB_USER` / `ATHLETE_LEDGER_RO_DB_PASSWORD` (both
     required; **never** fall back to the owner credentials) and the same DSN and wallet
     variables. Unqualified model tables must resolve to the owner schema: run
     `ALTER SESSION SET CURRENT_SCHEMA = <schema>` on each new connection (SQLAlchemy `connect`
     event), with `<schema>` from a new setting `ATHLETE_LEDGER_DB_SCHEMA` (default
     `ATHLETE_LEDGER`, validated as an unquoted identifier `^[A-Z][A-Z0-9_]{0,127}$` before use).
     Use `pool_pre_ping=True` for the long-running process.
   - SQLite: open the configured file **read-only** (`file:...?mode=ro` with `uri=true`); a
     missing file is a configuration error, never created.
   - New optional setting `ATHLETE_LEDGER_MCP_INCLUDE_ACTIVITY_NAMES` (bool, default `false`):
     activity names can contain places or people and tool results reach the client's model.
   - Add the new names to `.env.example` (empty values) and to *Configuration* in this file.
   - Queries select only the needed columns of the normalized tables and `sync_state`; never
     `raw_response.payload` or `params`. Reuse `athlete_ledger.coverage.sync_coverage`.

3. **Tools** (all four): annotations `readOnlyHint=true`, `idempotentHint=true`,
   `destructiveHint=false`, `openWorldHint=false`, plus a `title`. Pydantic result models so
   each tool has an `outputSchema` and returns structured content (with a compact text
   rendering if the SDK requires it). Descriptions are written for a language model: units,
   meaning of `null`, local-time semantics, limits. Dates are ISO `YYYY-MM-DD`; ranges are
   inclusive; `start > end`, malformed dates, and out-of-limit values return a tool error with
   a clear message (no stack traces, no SQL). Stable ordering everywhere.
   - `ledger_status()`: the server's local date; per dataset (`activities`, `sleep`, `hrv`,
     `stress`): covered intervals and gaps (from `sync_coverage`), `last_synced_date`,
     `last_run_at`, `last_status`, first and last date that has a record; the sports present in
     `activity` with their activity counts. This tells the model which dates it can trust.
   - `list_activities(start, end, sport=None, limit=50)`: `limit` 1–200. Activities whose local
     start date is in the range, ordered by `start_time_local`, then `activity_id`. Fields:
     `activity_id`, `start_time_local` (ISO, provider local wall time, no timezone), `sport`
     (`activity_type`, `null` if unknown), `distance_km`, `duration_min`, `avg_hr_bpm`,
     `calories_kcal`, and `activity_name` only when the setting is enabled (otherwise the key is
     absent). Also `total_matching`, `truncated`, and the downloaded coverage of the range.
   - `activity_totals(start, end, group_by)`: `group_by` is `week` (ISO week, key like
     `2026-W46` plus the Monday date), `month` (`2026-11`), or `sport` (`unknown` for null).
     Per group: `activity_count`, `distance_km`, `duration_h`, `calories_kcal`, and
     `avg_hr_bpm` weighted by duration over activities that have both values. Sums skip
     nulls; include, per group, how many activities lacked each value. Also overall totals and
     the downloaded coverage of the range.
   - `daily_wellness(start, end, areas=None)`: range at most 92 days; `areas` is any subset of
     `sleep`, `hrv`, `stress` (default all). One row per calendar day in the range, **including
     days without records**. Each area in a row has a `state`: `not_downloaded` (date outside
     that dataset's coverage), `no_record` (downloaded, no row), or `ok` (values present; any
     individual metric may still be `null`). Fields: sleep `sleep_min`, `deep_min`,
     `light_min`, `rem_min`, `awake_min`, `nap_min`, `sleep_score`, `avg_hr_bpm`,
     `avg_respiration_brpm`; HRV `last_night_avg_ms`, `weekly_avg_ms`,
     `last_night_5min_high_ms`, `baseline_low_ms`, `baseline_upper_ms`, `status`; stress
     `overall_stress_level` (0–100), `rest_min`, `low_min`, `medium_min`, `high_min`.
   Rounding: km to 2 decimals, minutes to 1, hours to 2, averages to 1.

4. **Server and CLI**: `athlete-ledger-mcp` runs the server over stdio (the only transport in
   this phase), with `--env-file PATH` (passed to the settings; default: `.env` in the current
   directory if present), because desktop clients start the process from an arbitrary directory.
   **stdout carries only the protocol**: no banner, no prints. Logs go to stderr at `WARNING` by
   default and never contain data values, SQL, or credentials. Configuration or connectivity
   failures at startup exit non-zero with the sanitized Phase 1a diagnostics on stderr; database
   failures during a call become tool errors with only an `ORA-`/`DPY-` code. Target protocol
   2026-07-28 as supported by the SDK; do not hand-roll protocol handling.

5. **Tests** (synthetic SQLite ledger only): exercise the server through the SDK's in-memory
   client if it has one (otherwise call the registered tools directly) plus one stdio
   subprocess smoke test. Cover: tool list with the four tools, annotations and output
   schemas; each tool's output on a seeded ledger with a coverage gap; the three wellness
   states; ISO week and month keys across a year boundary; duration-weighted HR; null
   handling; limit, truncation and the 92-day maximum; invalid input errors; activity names
   absent by default and present when enabled; SQLite read-only (a write attempt fails, a
   missing file is not created); Oracle read-only engine settings and the `CURRENT_SCHEMA`
   hook built without connecting (owner credentials ignored); stdout contains only protocol
   messages; no fixture values in logs.

6. **Docs**: README section "MCP server": what each tool returns, configuration for a desktop
   client (command, args with `--env-file`, no secrets in the client config), running the MCP
   Inspector against it, the read-only user on Oracle, and a privacy note: tool results are
   sent to whatever model the client uses.

Acceptance: local checks pass; the maintainer connects the MCP Inspector and a desktop client,
on SQLite and on ADB as `ATHLETE_LEDGER_RO`, and asks questions on real data.

### Activity training metrics, part 1 (done): schema migration

Branch `activity-metrics-schema`. Scope: the `core` database layer only. This patch adds columns
to `activity`; it does **not** change normalization, sync, or the MCP server. The new columns stay
NULL until part 2 (normalization mapping plus a `sync rebuild` command that re-derives the
normalized tables from `raw_response`, with no new downloads) and part 3 (MCP exposure). Decisions
already taken (do not revisit): one new Alembic revision, models and migration kept identical,
generated Oracle DDL regenerated, no hand-written DDL.

**New nullable columns on `activity`** (names carry units; source field in the provider's
`search_activities` record, for reference only; the mapping itself is part 2):

| Column | Type | Provider field |
|---|---|---|
| `aerobic_training_effect` | float | `aerobicTrainingEffect` (0.0–5.0) |
| `anaerobic_training_effect` | float | `anaerobicTrainingEffect` (0.0–5.0) |
| `training_effect_label` | string(64) | `trainingEffectLabel` |
| `training_load` | float | `activityTrainingLoad` |
| `training_stress_score` | float | `trainingStressScore` |
| `intensity_factor` | float | `intensityFactor` |
| `normalized_power_w` | float | `normPower` |
| `elevation_gain_m` | float | `elevationGain` |
| `elevation_loss_m` | float | `elevationLoss` |
| `max_hr_bpm` | float | `maxHR` |
| `avg_speed_mps` | float | `averageSpeed` (metres per second) |
| `moving_duration_s` | float | `movingDuration` |
| `moderate_intensity_min` | integer | `moderateIntensityMinutes` |
| `vigorous_intensity_min` | integer | `vigorousIntensityMinutes` |
| `hr_zone_1_s` … `hr_zone_5_s` | float (5 columns) | `hrTimeInZone_1` … `hrTimeInZone_5` |

Use the same SQLAlchemy type style as the existing metric columns (`Mapped[float | None]` /
`Mapped[int | None]`, `String(64)` for the label). No check constraints, no defaults, no indexes.

**Never add columns for** GPS coordinates (`startLatitude`, `startLongitude`, `endLatitude`,
`endLongitude`), `locationName`, owner identity (`ownerId`, `ownerFullName`,
`ownerDisplayName`, `ownerProfileImageUrl*`), or device identifiers. They stay only in
`raw_response`.

1. **Models** (`athlete_ledger.db.models.Activity`): add the 19 columns above.
2. **Migration** `0002_activity_training_metrics` (`down_revision = "0001_ledger_store"`):
   add the columns with `op.batch_alter_table("activity")` so the downgrade also works on SQLite;
   downgrade drops exactly these columns. No data changes, no grant changes: the existing
   table-level `SELECT` grant to `ATHLETE_LEDGER_READER` already covers new columns (state this
   in the migration's docstring). Like `0001`, the migration must not import the live models.
3. **First installation**: a new database gets everything by applying migrations to head, so
   `athlete-ledger db upgrade` and `scripts/create_tables.py` need no special path; verify that
   both reach `0002_activity_training_metrics` on an empty database. `sql/oracle/00_bootstrap.sql`
   is unchanged.
4. **Generated DDL**: regenerate `sql/oracle/schema.sql`; it must contain both revisions.
5. **Tests** (SQLite):
   - empty database → head: all 19 columns exist and are nullable;
   - upgrade from `0001` with synthetic `raw_response` and `activity` rows: rows and existing
     values preserved, new columns NULL;
   - downgrade to `0001`: the 19 columns are gone, existing data preserved; upgrade again works;
   - **model/migration parity**: Alembic `compare_metadata` between an upgraded database and
     `Base.metadata` reports no differences;
   - offline Oracle DDL contains the new `ALTER TABLE activity ADD` statements, and
     `schema.sql` is up to date;
   - no `activity` column name contains `lat`, `lon`, `location`, `owner`, or `device`;
   - `db check` reports `0002_activity_training_metrics`;
   - existing sync and MCP tests still pass unchanged.
6. **Docs**: update the revision named in README ("Database") and `docs/oracle-adb-setup.md`
   (`0001_ledger_store` → current head) and add a short "Upgrading an existing database" note:
   run `athlete-ledger db upgrade` (or `scripts/create_tables.py`) as the schema owner; the new
   columns stay empty until the next patch.

Acceptance: local checks pass; the maintainer upgrades ADB as the owner, `db check` shows
`0002_activity_training_metrics`, and `sync run` and the MCP server keep working.

### Activity training metrics, part 2 (done): mapping and rebuild

Branch `activity-metrics-rebuild`. Scope: the `core` package only (normalization, storage, sync
CLI). No schema changes, no MCP changes (part 3), no provider calls in the new command. Decisions
already taken (do not revisit): normalized tables are derived from `raw_response`; the new
`sync rebuild` command re-derives them from stored raw responses so a mapping change never needs
a new download; the upsert logic is shared between sync and rebuild, not duplicated.

1. **Mapping** (`athlete_ledger/normalize/garmin_connect.py`, activities only): fill the 19
   columns added by `0002_activity_training_metrics` from the provider record, keeping the
   existing value rules (missing, non-numeric, boolean, non-finite, or negative → NULL; zero is
   valid; integer columns round integral values):

   | Column | Provider field | Extra rule |
   |---|---|---|
   | `aerobic_training_effect`, `anaerobic_training_effect` | `aerobicTrainingEffect`, `anaerobicTrainingEffect` | outside 0.0–5.0 → NULL |
   | `training_effect_label` | `trainingEffectLabel` | string only; empty or longer than 64 characters → NULL (never truncate) |
   | `training_load` | `activityTrainingLoad` | |
   | `training_stress_score`, `intensity_factor`, `normalized_power_w` | `trainingStressScore`, `intensityFactor`, `normPower` | |
   | `elevation_gain_m`, `elevation_loss_m` | `elevationGain`, `elevationLoss` | |
   | `max_hr_bpm` | `maxHR` | |
   | `avg_speed_mps` | `averageSpeed` | metres per second, as provided |
   | `moving_duration_s` | `movingDuration` | |
   | `moderate_intensity_min`, `vigorous_intensity_min` | `moderateIntensityMinutes`, `vigorousIntensityMinutes` | integer |
   | `hr_zone_1_s` … `hr_zone_5_s` | `hrTimeInZone_1` … `hrTimeInZone_5` | seconds |

   Never read or map GPS coordinates, `locationName`, owner identity, profile image URLs, or
   device fields. Sleep, HRV and stress mappings are unchanged.

2. **Shared upsert** (`athlete_ledger/storage.py`): extract the "load by primary key, insert if
   absent, otherwise assign only changed columns" block of `store_response` into one function
   used by both sync and rebuild. Behaviour of `store_response` must stay identical (existing
   tests unchanged). `updated_at` and `source_response_id` change only when a value changes.

3. **Rebuild** (e.g. `athlete_ledger/rebuild.py`): for each selected dataset, read its
   `raw_response` rows (`provider = garmin_connect`, the dataset's endpoint) in ascending `id`
   order, i.e. fetch order, and normalize each payload with its stored `range_start`/`range_end`.
   Apply every non-empty raw response through the shared upsert in ascending id order:
   **last applied wins**. A record takes the values of the last raw response that contains it;
   later occurrences within a response also win. Sync applies every stored page with the same
   rule: no cross-page or within-chunk deduplication. Keep the provider's pagination no-progress
   check unchanged. Never infer chunk or run boundaries from ranges, offsets or empty pages.
   A second rebuild must report zero net inserts and updates. Rules:
   - read-only on `raw_response` and `sync_state`; never insert, update, or delete them;
   - never delete normalized rows (records absent from later raw responses stay, as in sync);
   - empty raw responses (`is_empty`) are skipped; a raw row with a null range, or a payload that
     fails validation, fails that dataset with a sanitized message naming the endpoint and the
     raw row `id` only;
   - one transaction per dataset: on failure roll back that dataset, continue with the others,
     exit non-zero;
   - stream raw rows (do not load every payload into memory at once).

4. **CLI**: `athlete-ledger sync rebuild [--dataset NAME ...]` (default all four). Like
   `sync status` and `sync coverage`, it reads only the database: no provider, no API key
   discovery, no network. Use the owner connection (it writes normalized tables). Output per
   dataset, counts only: raw responses read, empty skipped, rows inserted, updated, unchanged,
   placeholders skipped. Never print values, names, or payloads.

5. **Tests** (SQLite, synthetic data only):
   - mapping: every new field, including missing, negative, zero, boolean, non-numeric string,
     training effect above 5, label empty/too long, integral and non-integral intensity minutes;
     the normalized activity row contains no key for any excluded field;
   - rebuild fills the new columns of existing activities from stored raw responses, with a
     guard proving no provider is constructed and no API key is read;
   - a second rebuild reports zero inserts and zero updates;
   - two raw responses containing the same activity: the later `id` wins;
   - sleep/HRV/stress rebuild on unchanged mappings reports zero updates;
   - a corrupt raw payload in one dataset rolls back only that dataset, other datasets succeed,
     the exit code is non-zero, and the error shows only endpoint and raw id;
   - `raw_response` and `sync_state` are unchanged after a rebuild;
   - `sync run` on new synthetic raw data fills the new columns;
   - CLI output contains no fixture values.

6. **Docs**: README "Sync": what `sync rebuild` does, when to run it (after an upgrade that
   changes the mapping), that it needs no API key and makes no downloads, and that it must not run
   at the same time as a sync.

Acceptance: local checks pass; the maintainer runs `athlete-ledger sync rebuild` on ADB as the
owner, then a second rebuild reporting zero updates; the new activity columns are populated
(checked by counts only).

### Activity training metrics, part 3 (done): MCP exposure

Branch `activity-metrics-mcp`. Scope: the `mcp-server` package only. No schema, normalization or
sync changes; no new tools. Decisions already taken (do not revisit): the server still returns
**data, not analysis**: values are exposed as stored (with unit conversion and rounding only), and
totals are plain sums. No averages of training effect or intensity factor, no derived scores
(e.g. no "intensity minutes = moderate + 2 × vigorous", no acute/chronic load), no
interpretation of labels. Existing field names and meanings stay unchanged (additive change).

1. **`list_activities`**: add to each activity (names and units exactly as listed):
   - `moving_duration_min` (1 decimal), `avg_speed_kmh` (from m/s × 3.6, 1 decimal),
     `max_hr_bpm` (1 decimal), `elevation_gain_m`, `elevation_loss_m` (1 decimal);
   - `training`: object with `aerobic_effect`, `anaerobic_effect` (0.0–5.0, 1 decimal),
     `effect_label` (provider label string, as stored), `load` (1 decimal),
     `stress_score` (TSS, 1 decimal), `intensity_factor` (2 decimals),
     `normalized_power_w` (0 decimals);
   - `intensity_minutes`: object with `moderate`, `vigorous` (integers);
   - `hr_zone_min`: object with `zone_1` … `zone_5` (minutes in each heart-rate zone, 1 decimal).
   Every value may be `null`; the nested objects are always present (with null members) so the
   shape is stable. Keep the `activity_name` opt-in behaviour unchanged.
2. **`activity_totals`**: add to every group and to `overall` the plain sums
   `training_load` (1 decimal), `training_stress_score` (1 decimal), `moving_duration_h`
   (2 decimals), `elevation_gain_m` (1 decimal), `intensity_minutes` (`moderate`, `vigorous`,
   integers) and `hr_zone_h` (`zone_1` … `zone_5`, hours, 2 decimals). Same null rules as the
   existing sums (nulls skipped; all-null or empty → null), and extend `missing` with the count of
   activities lacking `training_load`, `training_stress_score`, `moving_duration`,
   `elevation_gain` and HR zones (an activity lacks zones if all five are null).
3. **Schemas and descriptions**: give every new output field a `Field(description=...)` with
   its unit (they appear in the tool `outputSchema`). Update the tool descriptions: list the new
   fields and units, say that training effect and load are the provider's own per-activity values,
   that TSS/IF/normalized power exist mostly for power-meter activities, and that totals are sums
   only. Keep descriptions concise; no Args/Returns/Raises text.
4. **Queries**: select only the needed `activity` columns; never `raw_response.payload` or
   `params` (the coverage query keeps reading only DISTINCT range_start/range_end via
   `athlete_ledger.coverage.sync_coverage`), never names unless enabled. Keep the existing
   coverage query unchanged. No change to the read-only engine.
5. **Tests** (synthetic SQLite only): every new field with unit conversion and rounding
   (including m/s → km/h and seconds → minutes/hours); null members and stable nested shape; sums
   and new `missing` counts per group and overall, including all-null groups; existing fields
   unchanged; `outputSchema` contains the new fields with descriptions; tool descriptions mention
   the new units and contain no Args/Raises text; no excluded field (GPS, location, owner, device)
   in any output; names still absent by default.
6. **Docs**: README "MCP server": the new fields with units, and a note that these values come
   from the provider and are summed, not interpreted.

Acceptance: local checks pass; the maintainer asks real questions through the MCP Inspector or a
desktop client (e.g. weekly training load over the last month, time in heart-rate zones per
week) on ADB as `ATHLETE_LEDGER_RO`.

### Multisport legs, part 1 (done): store the legs

Branch `multisport-legs-store`. Scope: the `core` package (schema, provider access,
normalization, sync, rebuild). No MCP changes (part 2). Decisions already taken (do not revisit):

- The provider's activity list (`search_activities`) returns a multisport activity
  (`activityType.typeKey = "multi_sport"`, `parent: true`) as **one summary with the totals of
  all its legs**; the legs are not in the list and the summary carries no leg identifiers.
- The legs are read with the SDK's `get_activity(activity_id)`: the parent's detail lists them in
  `metadataDTO.childIds`; each leg's detail is a complete activity with `parentId`,
  `activityTypeDTO.typeKey` and a `summaryDTO`. Transitions are legs too
  (`typeKey = "transition_v2"`).
- Legs go to a **separate table** `activity_leg`, never to `activity`: the parent already holds
  the totals, so `activity`, `list_activities` and `activity_totals` stay unchanged and nothing is
  counted twice. The hierarchy is the foreign key leg → parent.

Verified on 2026-11-17 against the maintainer's account (field names
and counts only, no values): 5 `multi_sport` activities in the ledger with `lapCount` 2–5 and no
leg identifiers; one parent detail with 5 `childIds` (running, transition_v2, cycling,
transition_v2, running), already in start order; every leg with `parentId` equal to the parent and
`isMultiSportParent: false`. The leg `summaryDTO` has **no** heart-rate zone, TSS or intensity
factor fields, and some names differ from the list record (`trainingEffect`, `normalizedPower`).

1. **Schema**: migration `0003_activity_legs` (`down_revision = "0002_activity_training_metrics"`),
   models kept identical, generated Oracle DDL regenerated, reader grant on the new table (same
   pattern as `0001`; downgrade revokes it and drops the table). Table `activity_leg`:

   | Column | Type | Notes |
   |---|---|---|
   | `provider`, `activity_id` | string(64), bigint | primary key; the leg's own provider id |
   | `parent_activity_id` | bigint, not null | with `provider`, foreign key to `activity`; indexed |
   | `leg_index` | integer, not null | 0-based position in the parent's `childIds` |
   | `start_time_local` | datetime, no timezone | `summaryDTO.startTimeLocal`; accept ISO 8601 with `T` or a space and optional fractional seconds; a value with a timezone offset is an unexpected response |
   | `activity_type` | string(128) | `activityTypeDTO.typeKey` |
   | `distance_m`, `duration_s`, `moving_duration_s` | float | `distance`, `duration`, `movingDuration` |
   | `avg_hr_bpm`, `max_hr_bpm`, `calories_kcal` | float | `averageHR`, `maxHR`, `calories` |
   | `avg_speed_mps` | float | `averageSpeed` (metres per second) |
   | `elevation_gain_m`, `elevation_loss_m` | float | `elevationGain`, `elevationLoss` |
   | `aerobic_training_effect`, `anaerobic_training_effect` | float | `trainingEffect`, `anaerobicTrainingEffect` (0.0–5.0) |
   | `training_effect_label` | string(64) | `trainingEffectLabel` |
   | `training_load` | float | `activityTrainingLoad` |
   | `normalized_power_w` | float | `normalizedPower` |
   | `moderate_intensity_min`, `vigorous_intensity_min` | integer | `moderateIntensityMinutes`, `vigorousIntensityMinutes` |
   | `source_response_id`, `updated_at` | as `SourceRecord` | provenance of the leg's detail response |

   Same value rules as the activity mapping (missing, non-numeric, boolean, non-finite or
   negative → NULL; zero is valid; training effect outside 0–5 → NULL; label never truncated).
   **Never** store leg names, GPS (`startLatitude`, `endLatitude`, …), `locationName`, owner,
   device, or any field not in the table: they stay only in `raw_response`.

2. **Provider and raw storage**: `PersonalProvider.activity_detail(activity_id)` calls the SDK's
   `get_activity` through the existing retry, backoff and token boundary and returns one
   `Response` like the dataset pages. Store every detail (parents and legs) as a `raw_response`
   row with the same fields as the dataset pages (`content_sha256`, `sdk_version`, `is_empty`,
   `fetched_at`) through a function next to `store_response`; upsert legs with the existing
   `upsert_row`. Raw rows: `endpoint = "get_activity"`, `params = {"activity_id": <id>}`,
   `range_start = range_end =` the parent's local start date.
   **Legs are not a dataset**: `DATASETS` is unchanged, there is no `sync_state` row for them,
   and `get_activity` rows never count as coverage (`sync coverage` and `ledger_status` read only
   the four dataset endpoints; add a test).

3. **Sync of legs** (`athlete_ledger/legs.py` or similar), run by `sync run` and `sync backfill`
   after the activities dataset **when `activities` is among the selected datasets**, and on its
   own by a new `athlete-ledger sync legs [--refresh]` (no `--dataset` option):
   - select the stored `multi_sport` parents that have **no stored parent-detail raw row** (a
     parent with zero legs is therefore not fetched again on every run); `--refresh` selects all
     `multi_sport` parents;
   - for each: fetch the parent detail, read `metadataDTO.childIds` (missing or empty → zero legs,
     not an error), fetch each leg's detail, normalize and upsert (`leg_index` from the `childIds`
     order);
   - unexpected responses fail that parent: a detail that is not an object or has no
     `summaryDTO`, a leg whose `parentId` differs from the parent, a leg id already stored under
     another parent, an invalid timestamp. Messages name the endpoint only, never values;
   - one transaction per parent (raw rows and legs together): a failure rolls back that parent
     only, the others go on, the command exits non-zero; the next run retries it;
   - output, counts only: parents checked, detail requests, legs inserted, updated, unchanged,
     parents failed. `sync status` also shows the number of stored legs.

4. **Rebuild**: `sync rebuild` re-derives `activity_leg` from the stored `get_activity` rows
   (parent details give the order, leg details the values), after `activities`, last applied wins
   as for the other datasets; no provider, no network, one transaction for the legs. `legs` is
   accepted by `sync rebuild --dataset` only (not by `backfill` or `run`), and the default rebuild
   includes it.

5. **Tests** (SQLite, synthetic fixtures only): migration up/down and model parity; grants in the
   offline Oracle DDL; mapping of every column including the value rules and the excluded fields;
   sync with a fake provider (parents without legs only; `--refresh`; a leg whose `parentId`
   differs, or a leg already under another parent → that parent fails and rolls back; empty
   `childIds` → zero legs and not fetched again without `--refresh`; no legs step when
   `activities` is not selected; `legs` refused by `backfill`/`run --dataset`); one transaction
   per parent;
   rebuild from raw rows gives the same rows and a second rebuild changes nothing; coverage and
   `ledger_status` ignore `get_activity` rows; CLI output has no values; existing tests unchanged.

6. **Docs** (the README is now an overview; the details live in `docs/`): `docs/sync.md` (what
   `sync legs` does, that it makes 1 + N requests per multisport activity, `--refresh`, and the
   `legs` rebuild), `docs/database.md` and `docs/oracle-adb-setup.md` (head revision
   `0003_activity_legs` and the upgrade note).

Acceptance: local checks pass; the maintainer upgrades ADB, runs `athlete-ledger sync legs` and
checks by counts that each `multi_sport` activity has as many legs as its `childIds`.

### Multisport legs, part 2 (done): MCP exposure

Branch `multisport-legs-mcp`. Scope: the `mcp-server` package only; additive change.

1. **`list_activities`**: every activity gets `legs`, a list ordered by `leg_index` (empty for
   activities that are not multisport or whose legs are not stored yet). Each leg has
   `activity_id`, `leg_index`, `sport` (from `activity_type`), `start_time_local`, and the same
   fields, units and rounding as an activity for the columns it has (`distance_km`,
   `duration_min`, `moving_duration_min`, `avg_hr_bpm`, `max_hr_bpm`, `calories_kcal`,
   `avg_speed_kmh`, `elevation_gain_m`, `elevation_loss_m`, `training` with `aerobic_effect`,
   `anaerobic_effect`, `effect_label`, `load`, `normalized_power_w`, and `intensity_minutes`).
   No `activity_name` for legs. One query for the legs of the selected activities (no query per
   activity).
2. **`activity_totals`**: unchanged; its description says that a multisport activity counts once,
   with the totals of all its legs.
3. **Descriptions and schemas**: document `legs` and its units in the tool description and the
   output schema; say that transitions appear as legs.
4. **Tests**: legs nested in order with conversions; empty `legs` elsewhere; totals unchanged with
   legs present; no excluded fields; one legs query per call.
5. **Docs**: `docs/mcp-server.md` (the `legs` field and its units; transitions as legs).

Acceptance: local checks pass; the maintainer sees the legs of a triathlon through the MCP
Inspector, and my-health (a separate project that reads this server) shows them in its day view.

### Sync for automation (current): env file, JSON output, lock

Branch `sync-automation`. Scope: the `core` CLI only; no schema, provider or normalization
changes. Goal: another local program (my-health, a separate project, or a scheduled job) can
start a sync without depending on its working directory, read the result as data, and never run
two syncs at once. The default text output and behaviour stay unchanged.

1. **`--env-file PATH`**, a global option before the command (`athlete-ledger --env-file
   PATH sync run`), as the MCP server already has. Settings are read from that file instead of
   `./.env`; environment variables still win, as before. A missing file is an error naming the
   option only (`Env file not found: --env-file.`), exit 1. Relative paths inside the file (for
   example the wallet directory) keep resolving against the working directory: document that
   automation should use absolute paths.
2. **`--json`** on `sync run`, `sync backfill`, `sync legs` and `sync status`: no banner and no
   text lines on stdout; exactly one JSON object, printed at the end. Diagnostics on stderr are
   unchanged. Shapes (keys always present):
   - sync commands: `{"command", "ok", "datasets": [{"dataset", "ok", "chunks", "requests",
     "raw_stored", "raw_unchanged", "rows_inserted", "rows_updated", "rows_unchanged",
     "placeholders_skipped", "error"}], "legs": null | {"ok", "parents_checked",
     "detail_requests", "legs_inserted", "legs_updated", "legs_unchanged", "parents_failed",
     "errors": [...]}}`; `sync legs` has `datasets: []`;
   - `sync status`: `{"command": "status", "ok": true, "datasets": [{"dataset",
     "last_synced_date", "last_run_at", "last_status"}], "legs_stored"}`;
   - any handled failure before or around the work (settings, database, provider setup, busy):
     `{"command", "ok": false, "error", "busy"}`, with the same sanitized message that goes to
     stderr.
   Messages are the existing safe ones (endpoint, HTTP status, type names): never values,
   names, keys, tokens or payloads. Exit codes are unchanged (0 or 1), except busy below.
3. **Lock**: `sync backfill`, `sync run`, `sync legs` and `sync rebuild` take an exclusive,
   non-blocking lock for the configured database before any provider or database work. A
   second process gets `Another sync or rebuild is already running for this database.` and
   **exit code 75** (`EX_TEMPFAIL`), with `busy: true` in JSON. The lock is an OS file lock
   (`fcntl.flock`, `msvcrt.locking` on Windows) on
   `$XDG_CACHE_HOME/athlete-ledger/sync-<key>.lock` (default `~/.cache/...`), where `<key>` is
   a short hash of the database identity (Oracle DSN, or the resolved SQLite path). It is
   released when the process ends, also on a crash. Limitation: it protects one machine only.
   `status` and `coverage` take no lock.
4. **Tests** (synthetic, no network): `--env-file` loads a file outside the working directory
   and a missing file fails without its path; JSON for `run`, `backfill` with a failed window,
   `legs`, `status`, and a settings error, all parseable and with no synthetic values; a held
   lock makes a second sync exit 75 and leaves the database untouched; the lock is released
   after a run; existing text output unchanged.
5. **Docs**: `docs/sync.md` (the three features, the JSON shapes, exit code 75, absolute paths
   for automation).

Acceptance: local checks pass; the maintainer runs `athlete-ledger --env-file <abs path> sync
run --json` from another directory on ADB, and a second run started meanwhile exits 75.

## Coding conventions

- **File header.** Every Python file (source, tests, scripts, Alembic migrations) starts with this
  module docstring, using the date of the file's last real modification (update it whenever you
  change the file). Only a shebang line may precede it. Indent continuation lines of the
  description by four spaces:

  ```python
  """
  Author: L. Saetta
  Date Last Modified: YYYY-MM-DD
  License: MIT
  Description: Brief description of this file's responsibilities.
  """
  ```

  The header is authorship metadata, not attribution: never name an AI tool in it (hard rule 0).
- **Google-style docstrings.** Every function, method, and class has a Google-style docstring,
  with `Args`, `Returns`, `Yields`, and `Raises` sections where they apply. This includes private
  helpers and test functions (a one-line docstring is enough for a test). Enforced by ruff's
  pydocstyle rules (`D`) with `convention = "google"`.

- Small, typed, documented functions; no network calls in unit tests (mock the SDK client).
- Timezone-aware datetimes for fetch timestamps; calendar dates as `datetime.date`.
- Errors: explicit exception types per package; never swallow exceptions silently.
- Commits: Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`, `test:`).
- Keep the documentation and `.env.example` in sync with behaviour. The README is an overview
  (goals, status, architecture, documentation index): keep its status table current, and put
  details in the dedicated guides under `docs/` (`quickstart.md`, `database.md`,
  `oracle-adb-setup.md`, `sync.md`, `mcp-server.md`, `development.md`).
