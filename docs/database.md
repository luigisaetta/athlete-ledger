# Database

athlete-ledger stores everything in one database accessed through SQLAlchemy 2.0. Two backends
are supported:

- **SQLite**, the default: a single local file, ideal for trying the project and for tests.
- **Oracle Autonomous Database** (ADB), connected with `python-oracledb` in thin mode over mTLS
  with a wallet. No Oracle Client installation is needed. Step-by-step provisioning is in
  [Oracle ADB setup](oracle-adb-setup.md).

The schema is defined once, in SQLAlchemy models, and evolved with packaged Alembic migrations.
Oracle DDL is generated from the same migrations, never written by hand (except the one-off
bootstrap script that creates users and roles).

## What is stored

| Table | Content |
|---|---|
| `raw_response` | Every provider response as received (JSON), with endpoint, request parameters, date range, fetch time, content hash and SDK version. Append-only. |
| `activity` | One row per activity: time, sport, distance, duration, HR, calories, and training metrics (training effect, load, TSS/IF/normalized power, elevation, max HR, speed, moving time, intensity minutes, time in HR zones). |
| `daily_sleep`, `daily_hrv`, `daily_stress` | One row per calendar day. |
| `sync_state` | Sync progress and last status per dataset. |

Normalized tables are always derived from `raw_response`, so they can be rebuilt without
downloading again (see [`sync rebuild`](sync.md#rebuild-from-stored-responses)). Every table has
a `provider` column, and units are part of column names (`distance_m`, `duration_s`, ...).
Metric columns are nullable: missing data is normal. Location, owner identity and device fields
are kept only in `raw_response` and are never normalized.

## Configuration

Settings come from environment variables and an optional `.env` in the current directory.
Environment variables take precedence; empty entries are ignored. The full list of names, with
empty values, is in [`.env.example`](../.env.example).

### SQLite

With no database variables set, SQLite uses `sqlite:///./data/athlete_ledger.db`, relative to
the current directory; parent directories are created when needed. To choose another file, set
`ATHLETE_LEDGER_DB_URL` to a SQLAlchemy SQLite URL. Prefer an absolute path if the MCP server will
use the same file.

### Oracle ADB

Oracle is selected when `ATHLETE_LEDGER_DB_DSN` is set. Do not set `ATHLETE_LEDGER_DB_URL` at
the same time: that is a configuration error.

| Variable | Purpose |
|---|---|
| `ATHLETE_LEDGER_DB_USER`, `ATHLETE_LEDGER_DB_PASSWORD` | Schema owner `ATHLETE_LEDGER` (migrations, sync, rebuild) |
| `ATHLETE_LEDGER_DB_DSN` | TNS alias from the wallet's `tnsnames.ora` |
| `ATHLETE_LEDGER_DB_WALLET_DIR` | Unzipped wallet directory, kept outside source control |
| `ATHLETE_LEDGER_DB_WALLET_PASSWORD` | Wallet password, required by thin-mode mTLS |
| `ATHLETE_LEDGER_RO_DB_USER`, `ATHLETE_LEDGER_RO_DB_PASSWORD` | Read-only user `ATHLETE_LEDGER_RO`, used only by the MCP server |
| `ATHLETE_LEDGER_DB_SCHEMA` | Owner schema the reader reads (default `ATHLETE_LEDGER`) |

Accounts follow least privilege:

- the owner has only `CREATE SESSION`, `CREATE TABLE`, `CREATE SEQUENCE`, `CREATE VIEW` and a
  quota on `DATA`;
- the role `ATHLETE_LEDGER_READER` receives `SELECT` on the ledger tables from the migrations;
- the reader `ATHLETE_LEDGER_RO` has only `CREATE SESSION` and that role, so read-only access is
  enforced by the database, not only declared by the MCP tools.

Users and roles are an administrator prerequisite. You can create them in the browser (see
[Oracle ADB setup](oracle-adb-setup.md)) or, with SQLcl connected as `ADMIN`, by running
[`sql/oracle/00_bootstrap.sql`](../sql/oracle/00_bootstrap.sql), which prompts for passwords with
hidden input and can be rerun safely.

## Create or upgrade the schema

The same commands create a new ledger and upgrade an existing one; Alembic applies only pending
migrations. On Oracle, run them as the schema owner.

```sh
athlete-ledger db upgrade
athlete-ledger db check
```

On ADB, `python scripts/create_tables.py` runs the same upgrade and check, and refuses to fall
back to SQLite if the Oracle DSN is missing. `db check` prints only the backend, server version
and current revision (`0002_activity_training_metrics` at the time of writing).

**Upgrade the database before using a new version of the code**: sync reads every column of the
normalized tables. On Oracle each DDL statement commits on its own, so an interrupted migration
may need manual inspection before retrying.

## Generated Oracle DDL

[`sql/oracle/schema.sql`](../sql/oracle/schema.sql) is the reviewable Oracle DDL generated from the
migrations, without connecting. Tests fail if it is out of date. Regenerate it after a migration
change:

```sh
athlete-ledger db sql --dialect oracle > sql/oracle/schema.sql
```

It documents the schema; do not apply it on top of a database managed with `db upgrade`.

## Storage notes

- JSON documents are stored as text on SQLite and as CLOB with an `IS JSON` check on Oracle,
  because the locked SQLAlchemy 2.0 dialect cannot compile native Oracle JSON. Documents are
  replaced as a whole.
- Fetch and update timestamps are stored as UTC and returned as timezone-aware UTC datetimes.
- `activity.start_time_local` is deliberately the provider's local wall time, without a timezone.

## Diagnostics

Commands print a short banner and `[OK]`/`[INFO]` labels in a terminal; redirected output has no
banner or colors, and `NO_COLOR=1` disables colors. Failures return a non-zero exit status and
show only safe details: the names of missing or invalid variables, an `ORA-nnnnn`/`DPY-nnnn` code,
or an `OSError` type. Error messages, settings values, paths, SQL and bound parameters are never
printed.
