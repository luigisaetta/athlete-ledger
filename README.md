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

For the complete Oracle procedure, follow [Oracle ADB setup](docs/oracle-adb-setup.md):
provisioning, browser-based user/role setup, wallet, `.env`, Python migrations and verification.
SQLcl is optional.

Database commands and setup scripts show a terminal banner and green `[OK]` labels for completed
steps. An unversioned schema is marked `[INFO]`. Redirected output has no banner or ANSI colors;
set `NO_COLOR=1` to disable colors in a terminal. `db sql` always emits only SQL.

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

For an **existing Oracle schema**, keep the unzipped wallet outside this repository and configure
the Oracle variables below. From the repository root, with the Conda environment active, run:

```sh
python scripts/create_tables.py
```

This creates/upgrades the six ledger tables and the Alembic revision table, then runs `db check`.
It uses Python thin-mode mTLS and requires no SQLcl installation. Re-running it applies only
pending migrations. It refuses the SQLite default when the Oracle DSN is missing, so an incomplete
configuration cannot silently create a local database. The schema owner must already have
`CREATE SESSION`, `CREATE TABLE`, `CREATE SEQUENCE`, `CREATE VIEW` and a quota on `DATA`.
The `ATHLETE_LEDGER_READER` role must also already exist because migrations grant it SELECT;
creating users/roles is an administrator prerequisite, not an action performed by this launcher.

For a **new schema**, the optional SQL bootstrap is available: using SQLcl, connect as `ADMIN`
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
messages, settings values, paths, SQL and bound parameters are not printed. Successful upgrades
report `0002_activity_training_metrics`; an empty database reports `unversioned`.

**Upgrading an existing database:** activate the project environment and run the upgrade as the
schema owner, then check the revision:

```sh
athlete-ledger db upgrade
athlete-ledger db check
```

On ADB, `python scripts/create_tables.py` performs the same upgrade/check sequence. Both fresh
and existing databases reach `0002_activity_training_metrics`; no separate installation path is
needed. This revision adds 19 nullable activity training metric columns, without changing existing
values or reader grants. The existing table-level SELECT grant covers the new columns. They stay
NULL immediately after the schema upgrade. Run `sync rebuild` to populate them from saved raw
responses using the current mapping; new downloads also fill them during normal sync. The existing
MCP tools do not expose these new metrics yet (part 3).

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


## Sync

Apply the database migrations first. Sync runs only in the CLI, using the official Personal API
SDK; the MCP server will read the ledger database only. Work with one sync process per database
at a time. This phase adds no schema changes.

Create a Personal API key for your own account using the
[official Personal API instructions](https://developer.garmin.com/gc-developer-program/personal/personal-faq/).
Set `GARMIN_PERSONAL_API_KEY` privately in your environment or local `.env`; the application passes
that secret explicitly to SDK discovery. With neither configured, the SDK can use its default
key file `~/.config/garmin/connect-personal-api-key`. Protect a key file with mode `0600`. Never
put credentials on the command line. Key validity and expiry are independent of bearer tokens.

The SDK caches bearer tokens in `~/.config/garmin/connect-personal-api-bearer-token.json`, outside
the repository. New cache files are created with mode `0600`; the adapter tightens permissions on
an existing cache before using it. A data request returning 401 triggers one forced token
exchange and retry. A second 401 or a 403 is fatal. Transient 429/5xx/timeout/connection failures
have up to three retries, waiting 2, 4 and 8 seconds. Authentication requests use the same transient
retry budget. Provider error messages never include response bodies, request URLs, keys or tokens.

Activate the Conda environment and run from the repository root. Backfill an explicit historical
range, replacing the sample dates with dates relevant to your own account:

```sh
athlete-ledger sync backfill --since 2026-01-01 --until 2026-01-14
athlete-ledger sync status
athlete-ledger sync run
```

`--until` defaults to the machine's local date. Future dates and reversed ranges are rejected.
By default all four datasets run: `activities`, `sleep`, `hrv`, `stress`. Limit a run with one or
more names; repeated `--dataset` options are also accepted:

```sh
athlete-ledger sync backfill --since 2026-01-01 --until 2026-01-14 --dataset sleep hrv
athlete-ledger sync run --dataset activities --dataset stress
```

Each dataset needs a successful backfill before its first incremental run. Incremental sync starts
at that dataset's last committed date minus two days, refreshing its last three synced calendar
days plus any newer days through today. If no committed date exists, the command reports that a
backfill is needed. `sync status` reads only dataset progress dates, run timestamps and status;
it does not discover credentials, construct a provider or contact the API.

Rebuild normalized records after upgrading to a version with changed field mappings:

```sh
athlete-ledger sync rebuild
athlete-ledger sync rebuild --dataset activities
```

`sync rebuild` uses the **owner** connection and reads only the existing database; it requires
no provider API key, constructs no provider and makes no downloads. It applies every non-empty
raw response in ascending `raw_response.id` order through the same upsert used by sync. The last
applied values win; it does not infer download/chunk boundaries from ranges, offsets or empty
pages. This fills the 19 activity training metric columns from `0002_activity_training_metrics`
when those fields are present in the stored provider summaries. Missing/invalid/negative values
remain NULL, zero is valid, training effects are limited to 0–5, and labels must be non-empty
strings of at most 64 characters (never truncated). Units stay as stored: speed in m/s, moving
and HR-zone durations in seconds, intensity minutes rounded to integers. Location, owner and
device fields are not mapped. Sleep, HRV and stress mappings are unchanged.

Replay is one transaction per dataset. Raw metadata is streamed and only one payload is fetched
at a time; the transaction retains normalized records and their initial scalar values. Every raw
occurrence is applied in order, with writes deferred until the final comparison. Row counters are
**net changes from transaction entry**, rather than historical intermediate transitions. Repeating
rebuild against unchanged raw history and mappings reports zero inserts/updates and preserves
update timestamps and provenance. The command never modifies `raw_response` or `sync_state`, and
never deletes normalized rows; records absent from later responses are retained.

Per dataset, output contains counts for `raw_read`, `empty_skipped`, `rows_inserted`,
`rows_updated`, `rows_unchanged` and `placeholders_skipped`. Empty responses are skipped. Null or
reversed stored ranges, invalid JSON or normalization failures roll back that dataset, continue
the remaining datasets and return nonzero. Failure diagnostics contain only the endpoint and
raw row ID; no payload, values, names, SQL or exception text. Failed-dataset row counters are
zero (rolled back), while raw/empty counts describe attempted reads. **Run only one sync or
rebuild process at a time for a database.** A rebuild does not change sync progress or coverage.

Inspect the downloaded date coverage without contacting the API. Like the other sync commands,
it shows the standard banner in an interactive terminal; redirected output omits the banner:

```sh
athlete-ledger sync coverage
athlete-ledger sync coverage --dataset sleep hrv
```

For each dataset, `sync coverage` merges overlapping or adjacent inclusive ranges and prints
`covered` intervals with `from`, `to` and calendar-day counts. It also shows each internal `gap`
and a separate `tail` from the last downloaded day plus one through the machine's local today.
No dates before the first downloaded interval are inferred. A dataset without ranges reports
`nothing downloaded` rather than guessing a historical start date. A single interval without
internal gaps uses `[OK]`; other datasets and gap/tail lines use `[INFO]`. A tail is informational
and may still follow an `[OK]` interval.

Coverage is derived from the committed `raw_response` chunk boundaries, filtered by provider
and dataset endpoint. The query selects only distinct `range_start`/`range_end` pairs, never JSON
payloads or request parameters. Normalized records are not used: a day with no activity, an empty
response or a missing daily summary can still belong to a successfully downloaded interval.
Failed chunks are rolled back and leave no new coverage; unchanged responses already have a
saved range. Coverage describes requested date ranges, not the number or completeness of records
returned by the provider. The progress date shown by `sync status` is only a high-water mark and
cannot reveal holes between older and newer downloads.

Each internal gap is followed by a dataset-specific backfill command. For example, if synthetic
sleep downloads cover January 1–7 and January 10–20, the missing January 8–9 range can be filled by:

```sh
athlete-ledger sync backfill --since 2026-01-08 --until 2026-01-09 --dataset sleep
athlete-ledger sync coverage --dataset sleep
```

The coverage command reads only the database, does not discover an API key or construct a
provider, and does not change sync progress. Only the suggested backfill command contacts the
API when you choose to run it. After the Phase 1b real-data checks, verify `sync coverage` on ADB
and confirm that the downloaded ranges, any internal gaps and the unsynced tail match your runs.

Requests use inclusive windows of at most seven days. Activities are paged with limit 100 through
an empty terminating page; the provider rejects pagination that makes no progress. Both sync
and rebuild use **last applied wins** for normalized values: each stored page is applied in order,
and a later occurrence of the same activity overrides earlier values, including repetitions
inside a single response. No cross-page or within-chunk first-occurrence filter is used. Every chunk commits raw responses, normalized rows and state together. On failure, that
chunk is rolled back, previous chunks remain committed, and a separate transaction records
`failed` without advancing its last committed date. Other datasets continue; any failure produces
a nonzero exit code. Older backfills preserve the later stored progress date, so they do not
move the next incremental window backwards; run timestamps and status still reflect the latest
attempt. Re-running an interrupted backfill is safe: completed chunks can be fetched
again, and identical raw responses are skipped. `sync run` resumes from the last committed date
with the normal overlap.

The ledger keeps raw responses append-only and hashes canonical UTF-8 JSON, comparing the latest
response with the same endpoint, range and SDK call parameters. Matching content skips storage
and normalization. Changed responses are stored before normalization; only rows whose normalized
values change get a new update timestamp and source-response reference. Records absent from a
later response are **not deleted**; provider-side deletions are outside this MVP. JSON documents
that are not valid serializable JSON are rejected rather than altering their raw content.

Activity `startTimeLocal` accepts ISO datetime strings with a space or `T` separator and optional
fractional seconds. It remains provider-local naive time: timestamps containing a timezone offset
or `Z` are rejected rather than silently converted or stripped of their timezone.

Output uses the terminal banner and `[OK]`/`[INFO]` helpers. Per dataset it shows chunk and request
counts, raw stored/unchanged counts, normalized rows inserted/updated/unchanged, and skipped
placeholder counts. Request counts include data-request retries and terminating empty activity
pages, but exclude token exchanges. Row counts describe rows actually normalized; responses
skipped by hash increment only `raw_unchanged`. Counts from a rolled-back chunk are discarded,
while its attempted requests remain in the request count. Metric values, activity names and
payloads are never printed. Full SDK logs are suppressed because their diagnostics can include
cache paths; only sanitized endpoint/method/status errors are displayed.

Local tests use fake clients and synthetic SQLite data, with guards against real HTTP calls and
credential discovery. Before Phase 2, the maintainer should verify on ADB a 14-day backfill,
repeat `sync run` against stable provider data and check `rows_updated=0`, then inspect
`sync status`. New/recalculated provider data can legitimately produce inserts or updates; zero
updates is expected only when normalized source values have not changed. See the SDK evidence in
[the reference notes](docs/reference/README.md#personal-api-sdk-source-verification-phase-1b).

## MCP server

`athlete-ledger-mcp` serves exactly four read-only, structured data tools over **stdio**.
It reads the ledger only; it never imports the sync adapter, discovers a provider key or
contacts the provider. Tool results contain data, not training advice or narrative analysis.

| Tool | Returned data |
|---|---|
| `ledger_status()` | Local today, per-dataset downloaded intervals and gaps, sync progress, first/last record dates and sports with activity counts. |
| `list_activities(start, end, sport=None, limit=50)` | Local-date activity summaries, optional exact sport filter, stable ordering, total match count, truncation and clipped download coverage. Limit: 1–200. |
| `activity_totals(start, end, group_by)` | Groups by ISO week (with Monday), calendar month or sport, overall totals, missing-value counts, duration-weighted HR and clipped coverage. |
| `daily_wellness(start, end, areas=None)` | Every day in an inclusive range of at most 92 days, with sleep/HRV/stress metrics and download/record state for each requested area. |

Dates use `YYYY-MM-DD` and include both endpoints. Activity start times are provider-local wall
time without a timezone. Results use km (2 decimals), minutes (1), hours (2), HR in beats/minute,
respiration in breaths/minute and HRV in milliseconds; averages have 1 decimal. Null means
unavailable; zero remains valid. Totals skip nulls and report missing counts for each input
metric. An empty/all-null sum is null. Weighted HR excludes activities missing either HR or
duration and is null when the eligible total duration is zero. Unknown sport groups use
`unknown`; actual sport fields remain null when unknown.

Wellness states distinguish `not_downloaded` (outside coverage), `no_record` (downloaded but no
normalized row) and `ok` (row exists; individual metrics may be null). `areas` defaults to all
three areas; an empty list returns dated rows with no selected areas. Requested-range coverage
includes missing dates at either edge as gaps; `ledger_status` shows internal gaps only.

Install/sync from the repository in the active Conda environment:

```sh
conda activate athlete-ledger
export UV_PROJECT_ENVIRONMENT="$CONDA_PREFIX"
uv sync --all-packages --inexact --locked
command -v athlete-ledger-mcp
```

Keep a private settings file outside source control, based on `.env.example`. For SQLite,
`ATHLETE_LEDGER_DB_URL` must identify an **existing** ledger file. Use an absolute SQLite path
when a desktop client may start in another directory, and quote a dotenv URL containing spaces
or `#`. Relative paths are resolved against the process working directory, not the settings
file's directory. SQLite is opened with URI `mode=ro`; missing files are rejected and never
created. Create/migrate/populate the ledger separately with the regular CLI.

For Oracle, configure `ATHLETE_LEDGER_RO_DB_USER` and `ATHLETE_LEDGER_RO_DB_PASSWORD` for the
separately provisioned **ATHLETE_LEDGER_RO** reader, plus the existing DSN, wallet directory and
wallet password variables. Both reader fields are required: owner credentials are ignored and
never used as fallback. `ATHLETE_LEDGER_DB_SCHEMA` defaults to `ATHLETE_LEDGER`; it accepts only
uppercase unquoted identifiers beginning with A–Z, followed by A–Z/0–9/underscore, at most 128
characters. Each new reader connection sets `CURRENT_SCHEMA` to this validated owner schema.
The reader must have SELECT grants on the ledger tables as provided by the bootstrap/migration.
The MCP process never runs migrations or grants.

Start the server with an absolute private settings-file path:

```sh
/absolute/path/to/conda/envs/athlete-ledger/bin/athlete-ledger-mcp --env-file /absolute/private/path/ledger.env
```

The default settings file is `.env` in the current directory if present. There is **no banner**
and no human-readable stdout: stdout is reserved for MCP. Warnings/diagnostics go to stderr,
with configuration/connection failures sanitized and returning nonzero. Database failures during
a tool call produce a tool error with only a fixed message and an optional ORA/DPY code. No SQL,
parameters, record values, credentials or tracebacks are logged. No telemetry exporter or HTTP
transport is configured; the process uses a no-op tracer provider.

In a desktop client's MCP server configuration, use the executable path reported above and an
absolute `--env-file` argument. A typical configuration is:

```json
{
  "mcpServers": {
    "athlete-ledger": {
      "command": "/absolute/path/to/conda/envs/athlete-ledger/bin/athlete-ledger-mcp",
      "args": ["--env-file", "/absolute/private/path/ledger.env"]
    }
  }
}
```

Keep secrets out of the client configuration. With Node.js/npm available, launch the official
[MCP Inspector](https://github.com/modelcontextprotocol/inspector) against the same command:

```sh
npx @modelcontextprotocol/inspector /absolute/path/to/conda/envs/athlete-ledger/bin/athlete-ledger-mcp --env-file /absolute/private/path/ledger.env
```

The Inspector is a separate local development client; the ledger server still uses stdio.
Verify that exactly the four tools appear with read-only annotations and output schemas. Test
both the Inspector and a desktop client against a populated local SQLite ledger and then ADB
as **ATHLETE_LEDGER_RO**. Ask, for example: “Which periods are downloaded?”, “List activities
between these dates”, “Show activity totals by ISO week” and “Show sleep and HRV day by day”.
Check gaps, nulls, units, truncated lists, ISO-week boundaries and days without records against
your own ledger. Keep real results and credentials private when reporting test outcomes.

**Privacy:** tool results are sent to whatever model the client uses. Activity names may contain
places or people; they are not selected and the `activity_name` key is absent by default.
Set `ATHLETE_LEDGER_MCP_INCLUDE_ACTIVITY_NAMES=true` in the private settings only if you intend
to include them. Metric values remain part of requested tool results even with names disabled.
Choose a client/model whose data handling meets your requirements.

## Disclaimer

athlete-ledger is an independent open-source project. It is **not affiliated with, endorsed by,
or sponsored by Garmin**. Garmin and Garmin Connect are trademarks of Garmin Ltd. or its
subsidiaries. Use of the Personal API is subject to Garmin's terms; it is intended for individuals
accessing their own data.

## License

[MIT](LICENSE)
