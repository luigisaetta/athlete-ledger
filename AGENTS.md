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
- `ATHLETE_LEDGER_DB_URL`: SQLAlchemy URL; default `sqlite:///./data/athlete_ledger.db`.
  For ADB: `oracle+oracledb://...` (credentials via the URL or separate variables below).
- `ATHLETE_LEDGER_DB_USER`, `ATHLETE_LEDGER_DB_PASSWORD`, `ATHLETE_LEDGER_DB_DSN`,
  `ATHLETE_LEDGER_DB_WALLET_DIR` (optional, mTLS only).
- `OCI_GENAI_BASE_URL`, `OCI_GENAI_API_KEY`, `OCI_GENAI_MODEL` (agent, Phase 4).

## Phases

The MVP is built in phases. Work only on the current phase. Each phase is one branch
(`phase-N-short-name`) and one PR whose description lists what was done, how it was tested,
and any open questions.

| Phase | Scope | Done when |
|---|---|---|
| **0. Foundation (current)** | Workspace, tooling, local checks, `docs/reference/`, `.env.example` | All acceptance checks pass locally on an empty skeleton |
| 1. Ledger and sync | `core`: Garmin access layer, SQLAlchemy schema, Alembic, `sync backfill --since` and incremental `sync` | Re-running sync creates no duplicates; tests green on SQLite (ADB tested by the maintainer) |
| 2. MCP over stdio | Read-only tools on the DB | Works with MCP Inspector and a desktop client |
| 3. Streamable HTTP | Stateless 2026-07-28 transport + 2025 compatibility | Conformance tests pass |
| 4. Agent | FastAPI `/chat` SSE, MCP client, OCI Responses tool loop | Answers questions on the four data areas citing dates and numbers |
| 5. UI | Next.js chat with tool-call cards | End-to-end demo works |
| 6. Launch prep | Docs, docker compose, packaging | Ready to publish |

### Phase 0 tasks (current)

1. Root `pyproject.toml` as a **uv workspace** with members `packages/core`, `packages/mcp-server`,
   `packages/agent`; shared dev dependency group (ruff, mypy, pytest, pre-commit); ruff and mypy config.
2. Three packages with `src/` layout, `__init__.py` exposing `__version__ = "0.0.1"`, a `py.typed`
   marker, and one trivial test each (e.g. version import). No feature code yet.
3. Console entry points reserved but minimal (print version and exit):
   `athlete-ledger` (core CLI), `athlete-ledger-mcp`, `athlete-ledger-agent`.
4. `.pre-commit-config.yaml` (ruff lint + format, end-of-file, trailing whitespace, check-yaml,
   detect-private-key).
5. **GitHub Actions is deferred and is not required to complete Phase 0.** Do not create or enable
   `.github/workflows/ci.yml` until the maintainer confirms that the repository has been configured
   to accept issues and PRs. Only after that confirmation, introduce CI on push and PR with
   `uv sync --locked`, ruff check, ruff format --check, mypy, and pytest on Python 3.12 and 3.13.
6. `.env.example` with the variable names listed above, values empty.
7. `docs/reference/README.md`: for each external spec, the official link plus a short summary
   **in your own words** of the rules that matter to this project (do not copy documentation text):
   MCP 2026-07-28 spec and changelog, MCP Python SDK 2.x migration guide, OCI Responses API,
   OCI OpenAI-compatible endpoints and tool support, Garmin Personal API FAQ and Swagger.
   Flag anything you could not verify.
8. Update the README "Development" section with the exact setup commands.

Acceptance, with `conda activate athlete-ledger` and
`export UV_PROJECT_ENVIRONMENT="$CONDA_PREFIX"`:

```sh
uv sync --all-packages --inexact --locked
uv run --all-packages --no-sync ruff check .
uv run --all-packages --no-sync ruff format --check .
uv run --all-packages --no-sync mypy
uv run --all-packages --no-sync pytest
```

All checks must pass locally. GitHub Actions remains deferred under task 5; a green CI run is not
a Phase 0 acceptance requirement. Runtime dependencies for later phases are added when their
features are implemented; Phase 0 packages have no runtime dependencies.

## Domain notes for Phase 1 (read before starting it)

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
