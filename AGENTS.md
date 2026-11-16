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

- `GARMIN_PERSONAL_API_KEY`: the SDK's standard variable (alternatively the SDK default key file).
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
| **1a. Ledger store (current)** | `core`: settings, SQLAlchemy engine (SQLite + Oracle mTLS), portable JSON type, models, Alembic, Oracle bootstrap SQL, generated Oracle DDL, `db` CLI | Migrations green on SQLite; Oracle DDL generated and reviewed; maintainer applies bootstrap + migrations on ADB and `db check` passes |
| 1b. Sync | `core`: Garmin access layer, normalization of the four data areas, `sync backfill --since` and incremental `sync` | Re-running sync creates no duplicates; tests green on SQLite (ADB tested by the maintainer) |
| 2. MCP over stdio | Read-only tools on the DB | Works with MCP Inspector and a desktop client |
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

### Phase 1a tasks (current): ledger store

Branch `phase-1a-ledger-store`. Scope is the `core` package only; no Garmin calls, no sync logic.
Decisions already taken (do not revisit): the schema is defined **once** in SQLAlchemy models and
evolved with Alembic, for portability; Oracle DDL is **generated**, never hand-written, except the
one-off bootstrap script; ADB connection is mTLS with a wallet.

1. **Dependencies** (`packages/core`): SQLAlchemy 2.0, Alembic, `python-oracledb`, Pydantic v2,
   `pydantic-settings`. Compatible ranges, lock in `uv.lock`. Do not add the Garmin SDK yet (1b).
2. **Settings** (`athlete_ledger.settings`): `pydantic-settings` model for the DB variables in
   *Configuration*; SQLite by default; Oracle when `ATHLETE_LEDGER_DB_DSN` is set; error if both
   URL and DSN are set, or if Oracle is selected without user, password, wallet dir, or wallet
   password. Secrets as `SecretStr`.
3. **Engine factory** (`athlete_ledger.db`): `create_engine` for SQLite (create the parent directory
   of the file if missing) and for `oracle+oracledb` in **thin mode**, passing `dsn`, `config_dir`,
   `wallet_location` (both the wallet dir), and `wallet_password` via `connect_args`. Do not call
   `oracledb.init_oracle_client()` (that is thick mode). No connection is opened at import time.
4. **Portable types** (`athlete_ledger.db.types`):
   - `JSONDocument`: round-trips `dict`/`list` on SQLite and Oracle. **First task of the phase:**
     check whether the locked SQLAlchemy version supports Oracle's native `JSON` type (ADB
     23ai/26ai). Use it if supported; otherwise fall back to a `TypeDecorator` over CLOB (or BLOB)
     with an `IS JSON` check constraint. Record the choice and the evidence in
     `docs/reference/README.md` and in the PR description.
   - `UTCDateTime`: timezone-aware in Python, stored as UTC on every backend (SQLite has no time
     zones); reject naive datetimes on write.
5. **Models** (`athlete_ledger.db.models`, typed `Mapped[...]`). Every table has a `provider`
   column (string, e.g. `garmin_connect`). Units are part of column names. Avoid Oracle reserved
   words as identifiers; unquoted lower-case names only.
   - `raw_response`: surrogate identity PK; `provider`, `endpoint`, `params` (JSON), `range_start`
     and `range_end` (dates, nullable), `fetched_at` (UTC), `content_sha256` (64 chars),
     `sdk_version`, `is_empty` (bool), `payload` (JSON, nullable). Append-only: no unique
     constraint on content; index on `(provider, endpoint, range_start)`.
   - `activity`: PK `(provider, activity_id)` (big integer); `start_time_local` (naive local time
     as returned by the provider, documented as such), `activity_name`, `activity_type`,
     `distance_m`, `duration_s`, `avg_hr_bpm`, `calories_kcal`, `file_status` (default
     `not_downloaded`), `source_response_id` (FK to `raw_response`), `updated_at` (UTC).
   - `daily_sleep`: PK `(provider, calendar_date)`; `sleep_time_s`, `deep_sleep_s`,
     `light_sleep_s`, `rem_sleep_s`, `awake_s`, `nap_time_s`, `sleep_score`, `avg_hr_bpm`,
     `avg_respiration_brpm`, `source_response_id`, `updated_at`.
   - `daily_hrv`: PK `(provider, calendar_date)`; `last_night_avg_ms`, `weekly_avg_ms`,
     `last_night_5min_high_ms`, `baseline_balanced_low_ms`, `baseline_balanced_upper_ms`,
     `hrv_status`, `source_response_id`, `updated_at`.
   - `daily_stress`: PK `(provider, calendar_date)`; `overall_stress_level` (0–100), `rest_s`,
     `low_stress_s`, `medium_stress_s`, `high_stress_s`, `source_response_id`, `updated_at`.
   - `sync_state`: PK `(provider, dataset)`; `last_synced_date`, `last_run_at` (UTC),
     `last_status`.
   All metric columns are nullable (missing data is normal). For reference, the demo scripts read
   these provider fields: activities `activityId`, `startTimeLocal`, `activityName`,
   `activityType.typeKey`, `distance`, `duration`, `averageHR`, `calories`; sleep `calendarDate`,
   `sleepTimeSeconds`, `deepSleepSeconds`, `lightSleepSeconds`, `remSleepSeconds`,
   `awakeSleepSeconds`, `napTimeSeconds`, `sleepScores.overall.value`, `avgHeartRate`,
   `averageRespirationValue`; HRV (`hrvSummaries[]`) `lastNightAvg`, `weeklyAvg`,
   `lastNight5MinHigh`, `baseline.balancedLow`, `baseline.balancedUpper`, `status`; stress
   `values.overallStressLevel`, `restStressDuration`, `lowStressDuration`,
   `mediumStressDuration`, `highStressDuration`. The mapping itself is Phase 1b.
6. **Alembic** inside the `core` package (migrations shipped with the package, configured
   programmatically from settings, not from a hand-edited `alembic.ini` URL). One initial
   migration creating all tables. When the dialect is Oracle, the migration also grants
   `SELECT` on every table to the role `ATHLETE_LEDGER_READER` (and revokes on downgrade).
7. **Oracle bootstrap** `sql/oracle/00_bootstrap.sql`, run once by the maintainer as `ADMIN` with
   SQLcl. Passwords come from substitution variables (prompted), never literals. It creates the
   owner `ATHLETE_LEDGER` (quota on `DATA`; `CREATE SESSION`, `CREATE TABLE`, `CREATE SEQUENCE`,
   `CREATE VIEW` only), the role `ATHLETE_LEDGER_READER`, and the user `ATHLETE_LEDGER_RO`
   (`CREATE SESSION` plus the role, nothing else). Idempotent where practical; header comment
   explains how to run it.
8. **Generated Oracle DDL** `sql/oracle/schema.sql`: produced by Alembic offline mode for the
   Oracle dialect (no connection). Add a CLI command to regenerate it and a test that fails if the
   committed file is out of date.
9. **CLI** (extend the `athlete-ledger` entry point, `argparse`, no extra framework):
   - `athlete-ledger db upgrade` — apply migrations to the configured database.
   - `athlete-ledger db sql [--dialect oracle|sqlite]` — print offline DDL to stdout.
   - `athlete-ledger db check` — connect, print backend, server version, and current Alembic
     revision; never print credentials or row contents. Non-zero exit on failure.
   Keep `athlete-ledger --version`.
10. **Tests** (SQLite only; no Oracle in tests): settings validation (defaults, Oracle selection,
    conflicting/missing values, secrets absent from `repr`), Oracle engine factory builds the right
    `connect_args` without connecting, migration upgrade/downgrade on a temporary SQLite file,
    model round-trip including `JSONDocument` and `UTCDateTime`, composite keys rejecting
    duplicates, offline Oracle DDL contains the tables and the reader grants, `schema.sql` up to date.
11. **Docs**: README section "Database" (SQLite quick start; ADB with wallet: bootstrap, env vars,
    `db upgrade`, `db check`); `.env.example` updated with the new variable names (empty values).

Acceptance: local checks pass; `athlete-ledger db upgrade` and `db check` work on SQLite; the PR
lists the JSON type decision and anything the maintainer must verify on ADB. The maintainer then
runs the bootstrap, `db upgrade`, and `db check` against ADB before Phase 1b starts.

## Domain notes for Phase 1b (read before starting it)

Garmin's SDK is a thin `requests` wrapper returning raw JSON. The `core` access layer must add:

- Bearer-token handling: tokens last 24 h, no refresh token; the client does **not** refresh on 401.
  Use `PersonalApiAuthenticator.obtain_token()` and `client.update_bearer_token()`; retry once on 401.
- Retry with backoff on 429 and 5xx (the SDK raises a single `HttpError` with `status_code`).
- Activity pagination with `start`/`limit` until an empty page; de-duplicate by `activityId`;
  fail if a page makes no progress.
- Date ranges split into inclusive 7-day chunks (stress allows at most 28 days).
- Response shapes differ: some endpoints return a list, others an object wrapping a list
  (e.g. `hrvSummaries`). Assign records by their `calendarDate`, never by position.
- Skip placeholder records with null `calendarDate`; treat negative sentinel values as missing;
  keep zeros as valid values.
- Store every response in `raw_response` (endpoint, params, fetched_at, content hash, SDK version,
  JSON) and derive normalized tables from it. Re-fetch the last 3 days on each sync; update rows
  only when the hash changes.

MVP data areas (summaries only): activities (`search_activities`), sleep
(`get_daily_sleep_summaries`), HRV (`get_daily_hrv_summaries`), stress (`get_daily_stress`).
Out of scope for the MVP: FIT files, intraday series, writing to Garmin, multiple users.

## Coding conventions

- Small, typed, documented functions; no network calls in unit tests (mock the SDK client).
- Timezone-aware datetimes for fetch timestamps; calendar dates as `datetime.date`.
- Errors: explicit exception types per package; never swallow exceptions silently.
- Commits: Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`, `test:`).
- Keep README and `.env.example` in sync with behaviour.
