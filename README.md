# athlete-ledger

**A local-first ledger of your training data, AI-ready via MCP.**

athlete-ledger keeps a faithful, local archive of your own training and wellness data and lets any
AI assistant that speaks the [Model Context Protocol](https://modelcontextprotocol.io) (MCP) answer
questions about it: *"How did I sleep before my long sessions this month?"*,
*"Compare my training volume per sport over the last two blocks"*, and so on.

> **Status: early development.** Nothing is usable yet. Interfaces, schema, and commands will change.

## Why

- **Official access, no password.** The first data provider is Garmin Connect, via the official
  [Personal API](https://developer.garmin.com/gc-developer-program/personal/personal-api) and
  Garmin's Python SDK. You create a personal API key in Garmin Connect; your account password is
  never used.
- **Local-first archive.** Data is synced incrementally into a database you control, so your history
  is available offline, fast to query, and not limited by API quotas or key expiry.
- **AI-ready, standard MCP.** A read-only MCP server (stdio and stateless Streamable HTTP, following
  the 2026-07-28 MCP specification) gives assistants compact, aggregated answers instead of raw JSON.
- **Not tied to one sport.** Running, cycling, swimming, triathlon, and any other endurance activity.
  The schema is designed so other providers can be added later.

## How it works

```
 sync CLI ──► Garmin Connect Personal API
    │
    ▼
 database (SQLAlchemy: Oracle Autonomous Database or SQLite)
    │   raw JSON responses + normalized daily/activity tables
    ▼
 MCP server (read-only)  ◄── agent (OCI Generative AI, Responses API) ◄── Next.js chat UI
                         ◄── any other MCP client (desktop assistants, IDEs, …)
```

| Component | Package | Role |
|---|---|---|
| Core | `athlete-ledger` | Provider access, database schema, sync CLI |
| MCP server | `athlete-ledger-mcp` | Read-only tools over the local database |
| Agent | `athlete-ledger-agent` | OpenAI-compatible agent on OCI Generative AI that uses the MCP tools |
| UI | `ui/` | Chat interface that shows every tool call |

## MVP scope

- Summary data only: activities, sleep, HRV, and stress.
- Databases: Oracle Autonomous Database (via `python-oracledb`) and SQLite.
- MCP transports: stdio and Streamable HTTP, bound to `localhost` by default.

Planned later: activity files (FIT) and intraday series, training-load analytics (CTL/ATL/TSB),
weekly reports to share with a coach, and more data providers.

## Privacy

Your data is health data, and athlete-ledger treats it that way:

- It runs on your machine; servers bind to `127.0.0.1` by default.
- No telemetry.
- MCP tools are read-only and query only your local database.
- Logs never contain the values of your data.

When you use the agent, your questions and the aggregated tool results are sent to the language
model you configure (for example OCI Generative AI in your own tenancy).

## Development

The project uses Python 3.12+, [Conda](https://docs.conda.io/) for the local environment, and
[uv](https://docs.astral.sh/uv/) for the Python workspace and lockfile. From the repository root:

```sh
conda env create --file environment.yml
conda activate athlete-ledger
export UV_PROJECT_ENVIRONMENT="$CONDA_PREFIX"
uv sync --all-packages --inexact --locked
pre-commit install
```

If the environment already exists, start at `conda activate athlete-ledger`. Activate it and export
`UV_PROJECT_ENVIRONMENT` in each new shell. This installs all three workspace packages and the shared
development tools into Conda. `--inexact` preserves Conda-managed bootstrap packages. Hooks use the
tools from this environment, so keep it active when committing. `uv.lock` pins workspace dependencies;
`environment.yml` defines the Python and uv bootstrap. No credentials are needed for Phase 0.

Run the local acceptance checks after syncing:

```sh
uv run --all-packages --no-sync ruff check .
uv run --all-packages --no-sync ruff format --check .
uv run --all-packages --no-sync mypy
uv run --all-packages --no-sync pytest
```

The reserved commands currently print version `0.0.1` and exit:

```sh
athlete-ledger
athlete-ledger-mcp
athlete-ledger-agent
```

The ledger store supports SQLite and Oracle ADB. Provider synchronization, the MCP server and
the agent will be implemented in later phases. Configuration names are listed with empty values
in `.env.example`. API references and verification gaps are in
[docs/reference/README.md](docs/reference/README.md).
Contributors and coding agents: see [AGENTS.md](AGENTS.md).

Acceptance currently uses local checks.

## Database

Activate the Conda environment as described above. With no database variables set, SQLite uses
`sqlite:///./data/athlete_ledger.db` relative to the current directory. Parent directories are created
when needed. Apply the packaged Alembic migrations, then check the database:

```sh
athlete-ledger db upgrade
athlete-ledger db check
athlete-ledger --version
```

Settings are read from environment variables and an optional `.env` in the current directory;
environment variables take precedence and empty environment-file entries are ignored. To use a
different SQLite file, set `ATHLETE_LEDGER_DB_URL` to its SQLAlchemy SQLite URL. Do not set it
together with `ATHLETE_LEDGER_DB_DSN`.

For Oracle ADB, keep the unzipped wallet outside this repository. Using SQLcl, connect as `ADMIN`
through the wallet and run [sql/oracle/00_bootstrap.sql](sql/oracle/00_bootstrap.sql). The script
prompts for owner and reader passwords with hidden input, creates `ATHLETE_LEDGER`, the
`ATHLETE_LEDGER_READER` role, and `ATHLETE_LEDGER_RO`. It assigns the owner a 1G quota on `DATA`
and only `CREATE SESSION`, `CREATE TABLE`, `CREATE SEQUENCE`, and `CREATE VIEW`. The reader gets
only `CREATE SESSION` and the reader role. Reruns preserve existing passwords and reapply the
quota/grants; they do not remove pre-existing privileges. Use dedicated accounts and review their
privileges if they already existed. Follow the script's supported password character set and ADB
complexity rules; keep spooling/session recording off during bootstrap.

Configure the following privately in your environment or local `.env`, then run the same
`db upgrade` and `db check` commands as the schema owner:

| Variable | Purpose |
|---|---|
| `ATHLETE_LEDGER_DB_USER` | `ATHLETE_LEDGER` schema owner |
| `ATHLETE_LEDGER_DB_PASSWORD` | Owner password |
| `ATHLETE_LEDGER_DB_DSN` | TNS alias from the wallet's `tnsnames.ora` |
| `ATHLETE_LEDGER_DB_WALLET_DIR` | Directory containing `tnsnames.ora` and `ewallet.pem` |
| `ATHLETE_LEDGER_DB_WALLET_PASSWORD` | Wallet password for thin-mode mTLS |
| `ATHLETE_LEDGER_RO_DB_USER` | `ATHLETE_LEDGER_RO`, reserved for the later MCP server |
| `ATHLETE_LEDGER_RO_DB_PASSWORD` | Reader password, reserved for the later MCP server |

Leave `ATHLETE_LEDGER_DB_URL` unset for Oracle. The engine uses `oracle+oracledb` in thin mode;
the wallet directory is passed as both `config_dir` and `wallet_location`. No Oracle Client
installation or `init_oracle_client()` call is needed. `db check` prints only backend, server
version and Alembic revision. Failures return a nonzero exit status: settings diagnostics list only
the missing or invalid environment variable names; database diagnostics show only an available
`ORA-nnnnn` or `DPY-nnnn` code; filesystem diagnostics show only the `OSError` type. Original error
messages, settings values, paths, SQL and bound parameters are not printed. Successful Phase 1a
upgrades report `0001_ledger_store`; an empty database reports `unversioned`.

Generate the reviewable Oracle schema from Alembic without connecting:

```sh
athlete-ledger db sql --dialect oracle > sql/oracle/schema.sql
athlete-ledger db sql --dialect sqlite
```

The snapshot is checked by tests; regenerate it after migration changes. It is an offline
representation of the same migrations, not an additional script to apply after `db upgrade`.
Oracle DDL auto-commits, so an interrupted migration may require inspection before retrying.

Documents use text storage on SQLite and CLOB with `IS JSON` constraints on Oracle, because the
locked SQLAlchemy 2.0 dialect cannot compile native JSON. Replace JSON documents as a whole;
in-place mutations are not tracked. Fetch/update timestamps round-trip as aware UTC datetimes,
stored as naive UTC (`TIMESTAMP` on Oracle). Activity `start_time_local` is deliberately a naive
provider-local wall time and does not identify a timezone.

Before starting sync development, the maintainer must verify bootstrap and grants on ADB,
wallet mTLS, repeatable `db upgrade`, and `db check`. Also test synthetic object/list JSON
round-trips (including Unicode, SQL NULL and a document larger than 32 KiB), invalid JSON rejection,
UTC timestamps, and reader access: qualified `SELECT` on the owner tables succeeds while DML and
DDL fail. Reader sessions use `ATHLETE_LEDGER.<table>`; no synonyms or default-schema changes are
created. These live Oracle checks have not been run by the test suite.

## Disclaimer

athlete-ledger is an independent open-source project. It is **not affiliated with, endorsed by,
or sponsored by Garmin**. Garmin and Garmin Connect are trademarks of Garmin Ltd. or its
subsidiaries. Use of the Personal API is subject to Garmin's terms; it is intended for individuals
accessing their own data.

## License

[MIT](LICENSE)
