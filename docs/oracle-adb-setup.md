# Oracle Autonomous Database setup

Step-by-step setup for athlete-ledger on Oracle Autonomous Database (ADB), using the OCI Console,
Database Actions in the browser, and the project's Python tools. **No SQLcl or Oracle Instant
Client installation is required.**

Reviewed: 2026-11-16. Examples contain only generic identifiers and empty configuration values.
The application has been tested locally on SQLite; the maintainer must perform the live ADB
checks below. OCI labels and available database versions can differ by region.

## 1. Choose the starting point

| What already exists | Where to start |
|---|---|
| No ADB instance | Step 2 |
| ADB exists, but no application schema | Step 3 |
| Application schema already exists | Step 3 to verify its grants and reader role, then Step 4 |
| Schema, grants, wallet and local environment are ready | Step 6 |

There are three database identities in this guide:

| Identity | Purpose |
|---|---|
| `ADMIN` | One-time administration in Database Actions |
| `ATHLETE_LEDGER` | Schema owner; applies migrations and will run synchronization |
| `ATHLETE_LEDGER_RO` | Separate read-only account for later MCP use and permission tests |

`ATHLETE_LEDGER_READER` is a **role**, not a login. The migrations grant it SELECT on the tables.
The role must exist before migrations, even if the read-only account is being deferred.

If using another existing owner name, replace `ATHLETE_LEDGER` in the administrative and
verification SQL with that owner, and use the same name in `ATHLETE_LEDGER_DB_USER`. Keep the
role name `ATHLETE_LEDGER_READER`: it is fixed in the current migrations.

## 2. Create the ADB instance, if needed

Use your personal OCI tenancy and a compartment intended for this project.

1. In the OCI Console, choose the region and compartment.
2. Open **Oracle AI Database → Autonomous AI Database → Create Autonomous AI Database**.
3. Choose Serverless and the Transaction Processing workload. Select the available 23ai/26ai
   database version appropriate to your tenancy; current consoles may label it 26ai.
4. Enter a display name and database name. If using Always Free, explicitly select that option
   when available and review the resulting resource configuration before creating the instance.
5. Set and retain the `ADMIN` password privately.
6. Configure network access reachable from your computer. For a public endpoint, restrict access
   to the intended client IP addresses. For a private endpoint, your computer needs the relevant
   private network/VPN route. Keep mutual TLS enabled.
7. Create the instance and wait until its state is **Available**.

If the instance already exists, verify its state and network path rather than recreating it.
See Oracle's [provisioning guide](https://docs.oracle.com/en-us/iaas/autonomous-database-serverless/doc/autonomous-provision.html).

## 3. Prepare the schema and reader role in the browser

### 3.1 Open Database Actions as ADMIN

From the ADB details page, open **Database actions → SQL** (or open all actions and select SQL).
Sign in as `ADMIN` if prompted. This is the browser-based SQL Worksheet; nothing needs to be
installed locally. See [accessing Database Actions](https://docs.oracle.com/en-us/iaas/autonomous-database-serverless/doc/connect-database-actions.html).

### 3.2 Create the owner only if it does not already exist

In Database Actions, open **Administration → Database Users → Create User**:

1. Set the username to `ATHLETE_LEDGER`.
2. Enter and confirm its password in the password fields, following the current ADB password
   policy. Retain it privately for the local `.env`.
3. Give it a quota on tablespace `DATA`; this guide uses **1 GB**.
4. Leave optional features such as Web Access, Graph and OML disabled for this application account.
5. Review Granted Roles. Do not add broad convenience roles such as `DWROLE` or `RESOURCE`.
6. Create the user and confirm that its account is open.

If the user already exists, preserve its password and data. Review its account status and quota.
Web Access can add convenience roles automatically, so review existing roles rather than assuming
an account created through the UI has only the privileges needed here.

Passwords are entered through the user form so they do not need to appear in saved SQL scripts.
See [creating users](https://docs.oracle.com/en-us/iaas/autonomous-database-serverless/doc/manage-users-create.html).

### 3.3 Create the required role and owner privileges

Back in the SQL Worksheet as `ADMIN`, execute this administrative bootstrap block using the
worksheet's script execution action:

```sql
DECLARE
    existing_count PLS_INTEGER;
BEGIN
    SELECT COUNT(*) INTO existing_count
      FROM dba_roles WHERE role = 'ATHLETE_LEDGER_READER';
    IF existing_count = 0 THEN
        EXECUTE IMMEDIATE 'CREATE ROLE ATHLETE_LEDGER_READER';
    END IF;
END;
/

GRANT CREATE SESSION, CREATE TABLE, CREATE SEQUENCE, CREATE VIEW TO ATHLETE_LEDGER;
ALTER USER ATHLETE_LEDGER DEFAULT TABLESPACE DATA;
ALTER USER ATHLETE_LEDGER QUOTA 1G ON DATA;
```

This block creates no ledger tables, changes no passwords, and is safe to repeat for the intended
owner/role. It does not remove pre-existing grants. Schema tables are created only through the
packaged Alembic migrations in Step 8. The finite quota is deliberate; do not substitute broad
roles or unlimited tablespace privileges just to get past a setup error.

Oracle documents [individual grants and DATA quotas](https://docs.oracle.com/en-us/iaas/autonomous-database-serverless/doc/manage-users-privileges.html).

### 3.4 Create the read-only account now, or defer it

For the first owner connection and migration test, you may defer this account. **Do not defer the
role in Step 3.3.**

To prepare it now, use the same Database Users form to create `ATHLETE_LEDGER_RO`, with a distinct
password. Do not enable optional features, grant broad roles, or give it a DATA quota. Then, as
`ADMIN`, run:

```sql
GRANT CREATE SESSION TO ATHLETE_LEDGER_RO;
GRANT ATHLETE_LEDGER_READER TO ATHLETE_LEDGER_RO;
```

The reader needs a different identity from the owner to test actual read-only permissions. The
owner `.env` configuration continues to use `ATHLETE_LEDGER`, not the reader or `ADMIN`.

### 3.5 Verify administration before creating tables

As `ADMIN`, inspect metadata only:

```sql
SELECT username, account_status, default_tablespace
FROM dba_users
WHERE username IN ('ATHLETE_LEDGER', 'ATHLETE_LEDGER_RO');

SELECT role FROM dba_roles WHERE role = 'ATHLETE_LEDGER_READER';

SELECT grantee, privilege FROM dba_sys_privs
WHERE grantee IN ('ATHLETE_LEDGER', 'ATHLETE_LEDGER_RO')
ORDER BY grantee, privilege;

SELECT grantee, granted_role FROM dba_role_privs
WHERE grantee IN ('ATHLETE_LEDGER', 'ATHLETE_LEDGER_RO')
ORDER BY grantee, granted_role;

SELECT username, tablespace_name, max_bytes FROM dba_ts_quotas
WHERE username = 'ATHLETE_LEDGER' AND tablespace_name = 'DATA';
```

Expect the reader role to exist, the owner to have the four CREATE privileges above and a DATA
quota, and the reader (if created) to have CREATE SESSION plus the reader role. Investigate extra
privileges on reused accounts; this setup does not revoke them automatically.

## 4. Prepare the local Python environment

From the repository root:

```sh
conda env create --file environment.yml
conda activate athlete-ledger
export UV_PROJECT_ENVIRONMENT="$CONDA_PREFIX"
uv sync --all-packages --inexact --locked
```

If the Conda environment already exists, skip its creation and start with activation. Activate it
and export `UV_PROJECT_ENVIRONMENT` in every new terminal. All following local commands run from
the repository root, because `.env` is loaded relative to the current directory.

## 5. Download and unpack the wallet

1. On the ADB details page, open **Database connection**.
2. Choose **Instance wallet**, then **Download wallet**.
3. Set a wallet password and retain it privately. It protects the wallet and is separate from the
   owner, reader and ADMIN passwords.
4. Download the ZIP and extract it outside the repository, for example under
   `~/.config/athlete-ledger/wallet`.
5. Locate the directory that directly contains `tnsnames.ora` and `ewallet.pem`. Pointing to the
   ZIP file, or to a parent containing another nested wallet directory, will not work.
6. Open `tnsnames.ora` locally to identify a service alias, such as the synthetic example
   `example_low` or `example_medium`. Do not copy its contents into issues or this documentation.

Use an absolute directory path in `.env`. Keep wallet files private to your local account.
The repository ignores `wallet/` and `Wallet_*/`, but the documented location is outside Git.
If the extracted wallet lacks `ewallet.pem`, obtain a compatible wallet or follow the driver's
[PKCS12-to-PEM guidance](https://python-oracledb.readthedocs.io/en/stable/user_guide/connection_handling.html#mutual-tls-mtls-connection-to-oracle-autonomous-database)
before continuing; this project does not convert wallet formats automatically.

See Oracle's [wallet download steps](https://docs.oracle.com/en-us/iaas/autonomous-database-serverless/doc/connect-download-wallet.html)
and [Python mTLS connection guide](https://docs.oracle.com/en-us/iaas/autonomous-database-serverless/doc/connecting-python-mtls.html).

## 6. Configure the local .env

If `.env` does not yet exist, copy `.env.example` to `.env`. **Preserve an existing `.env`.** Edit it
locally; do not put passwords on a command line. Fill these names (the block deliberately contains
no values):

```dotenv
ATHLETE_LEDGER_DB_URL=
ATHLETE_LEDGER_DB_USER=
ATHLETE_LEDGER_DB_PASSWORD=
ATHLETE_LEDGER_DB_DSN=
ATHLETE_LEDGER_DB_WALLET_DIR=
ATHLETE_LEDGER_DB_WALLET_PASSWORD=
ATHLETE_LEDGER_RO_DB_USER=
ATHLETE_LEDGER_RO_DB_PASSWORD=
```

| Variable | Value to enter privately |
|---|---|
| `ATHLETE_LEDGER_DB_URL` | Leave empty for Oracle |
| `ATHLETE_LEDGER_DB_USER` | Owner username, normally `ATHLETE_LEDGER` |
| `ATHLETE_LEDGER_DB_PASSWORD` | Owner password, not the ADMIN password |
| `ATHLETE_LEDGER_DB_DSN` | Exact TNS alias from the wallet, or full connect descriptor |
| `ATHLETE_LEDGER_DB_WALLET_DIR` | Absolute path to the extracted directory containing both files |
| `ATHLETE_LEDGER_DB_WALLET_PASSWORD` | Password supplied when downloading the wallet |
| `ATHLETE_LEDGER_RO_DB_USER` | `ATHLETE_LEDGER_RO`, or leave empty while deferring reader tests |
| `ATHLETE_LEDGER_RO_DB_PASSWORD` | Reader password, or leave empty while deferring reader tests |

For a full descriptor, copy it from the connection information and keep it on one quoted line.
Do not substitute a JDBC URL. A TNS alias is shorter and avoids manual descriptor editing.
Do not omit DB_DSN: the general CLI defaults to SQLite when it is absent. The creation script
refuses that fallback. URL and DSN cannot both be configured.

Environment variables already exported in the terminal take precedence over `.env`. If a change
to `.env` has no effect, remove stale exported project variables before retrying. Empty entries
are ignored. Quote values where needed and preserve the exact password characters; `.env` is
read by the settings loader, not executed as a shell script.

No Garmin or OCI model API key is needed to set up the database.

## 7. Test connectivity without creating tables

Run as the configured owner:

```sh
athlete-ledger db check
```

Verify that it reports **Backend: oracle** and a server version. For a new schema, the revision
should be **unversioned**. If it reports SQLite, stop and correct Step 6 before any upgrade.

This reads database metadata only. It does not authenticate as ADMIN, create users, apply migrations,
or read training payloads. Errors are intentionally limited to variable names, an available
ORA/DPY code, or an OSError type; see Step 11.

## 8. Create the ledger tables with Python

After confirming the role and quota in Step 3, run:

```sh
python scripts/create_tables.py
```

The script calls the existing Alembic upgrade, then `db check`. It prints a migration confirmation
and should finish with:

```text
  [OK] Database migrations applied.
  [OK] Database connection verified.
       Backend: oracle
       Server version: <your database version>
  [OK] Alembic revision: 0001_ledger_store
  [OK] Oracle schema setup completed.
```

On a terminal, a banner precedes these results and `[OK]` is green. Redirected output is plain
text without the banner. Set `NO_COLOR=1` to disable terminal colors. A connection to an empty
schema reports the revision as `[INFO] ... unversioned`, not as a migration success.

It creates six ledger tables plus Alembic's version table:

- `raw_response`
- `activity`
- `daily_sleep`
- `daily_hrv`
- `daily_stress`
- `sync_state`
- `alembic_version`

It also grants SELECT on all seven tables to `ATHLETE_LEDGER_READER`. The existing schema owner
remains the owner; no users or roles are created by this script. Do not run migrations as ADMIN.

[sql/oracle/schema.sql](../sql/oracle/schema.sql) is the generated review snapshot. **Do not apply
it again after the Python migration**, and do not hand-create the ledger tables from examples.

## 9. Verify repeatability and the generated schema

Run the creation script again, then check independently:

```sh
python scripts/create_tables.py
athlete-ledger db check
```

Both should succeed with revision `0001_ledger_store`. A successful repeat applies no new migration
and creates no duplicate tables. This does not promise automatic recovery from a failed first run:
Oracle DDL auto-commits and can leave partial objects behind.

As ADMIN in Database Actions, inspect the owner metadata:

```sql
SELECT table_name FROM dba_tables
WHERE owner = 'ATHLETE_LEDGER'
  AND table_name IN ('RAW_RESPONSE', 'ACTIVITY', 'DAILY_SLEEP', 'DAILY_HRV',
                     'DAILY_STRESS', 'SYNC_STATE', 'ALEMBIC_VERSION')
ORDER BY table_name;

SELECT version_num FROM ATHLETE_LEDGER.alembic_version;

SELECT column_name, data_type FROM dba_tab_columns
WHERE owner = 'ATHLETE_LEDGER' AND table_name = 'RAW_RESPONSE'
  AND column_name IN ('PARAMS', 'PAYLOAD', 'FETCHED_AT');

SELECT constraint_name, status FROM dba_constraints
WHERE owner = 'ATHLETE_LEDGER' AND table_name = 'RAW_RESPONSE'
  AND constraint_name IN ('CK_RAW_RESPONSE_PARAMS_JSON', 'CK_RAW_RESPONSE_PAYLOAD_JSON');
```

Expect seven table names and one revision row. PARAMS/PAYLOAD use CLOB, with both JSON constraints
enabled. FETCHED_AT uses TIMESTAMP. The locked SQLAlchemy dialect does not compile Oracle native
JSON; the evidence is in [the reference notes](reference/README.md#ledger-storage-oracle-json-and-mtls-phase-1a).

## 10. Verify reader grants and finish ADB acceptance

As ADMIN, check the role grants:

```sql
SELECT table_name, privilege FROM dba_tab_privs
WHERE owner = 'ATHLETE_LEDGER' AND grantee = 'ATHLETE_LEDGER_READER'
ORDER BY table_name, privilege;
```

Expect SELECT on the seven tables and no INSERT/UPDATE/DELETE grants from this setup. Also review
reader system privileges and granted roles using Step 3.5. Reader queries must qualify tables as
`ATHLETE_LEDGER.<table>`: the project creates no synonyms or default-schema changes.

For the live read-only acceptance test, connect through a Python client as the distinct reader,
using its own credentials and the same wallet. Verify that a qualified SELECT succeeds and a
zero-row statement such as `DELETE FROM ATHLETE_LEDGER.raw_response WHERE 1 = 0` is denied. This
statement needs DELETE privilege even though it would affect no rows. Verify the absence of DDL
privileges as well. Do not use the owner account to conclude that reader restrictions work.
The normal `db check` is an owner-schema check, not a cross-schema reader-permission test.

Before starting Phase 1b, use synthetic records to test object/list JSON round-trips, Unicode,
SQL NULL, a document larger than 32 KiB, and invalid JSON rejection. Check identity generation,
foreign keys, provider-scoped composite keys, nullable/zero metrics, and UTC round-trips including
fractional seconds. Fetch/update timestamps are aware UTC in Python; `start_time_local` deliberately
remains naive provider-local wall time. The current launcher creates the schema; it does not run
these data tests or insert sample training records.

## 11. Troubleshooting

| Symptom | What to check |
|---|---|
| Missing/invalid environment variable names | Fill the named settings; the CLI never prints their values |
| Backend is SQLite | DB_DSN is missing/empty, or the wrong working directory/.env was used |
| Wallet directory exists but connection fails | The path must directly contain `tnsnames.ora` and `ewallet.pem`; check the wallet password |
| `FileNotFoundError` / `PermissionError` | Local directory existence and access, without printing wallet contents |
| `ORA-01919` | Reader role is missing: complete Step 3.3 before migrations |
| `ORA-01950` | Owner DATA quota is missing or exhausted: ask ADMIN to review Step 3 |
| `ORA-00955` after a previous failed upgrade | Existing objects may come from a partial migration or manual DDL |
| Other ORA/DPY codes | Look up the exact code in Oracle/driver documentation; check instance state, DSN, network route and credentials privately |

For a partial migration, inspect object metadata and the Alembic revision as ADMIN before retrying.
Do not stamp the revision, drop tables, or assume that an automatic rollback removed DDL. A
maintainer must decide how to reconcile existing objects, especially if any data is present.

Share only sanitized codes/statuses in issue reports. Keep `.env`, passwords, wallet files and
health payloads out of commits and screenshots. The optional
[SQLcl bootstrap](../sql/oracle/00_bootstrap.sql) remains available for administrators who already
use SQLcl; it is not needed for the browser-and-Python procedure above.

Oracle error references: [ORA-01919](https://docs.oracle.com/en/error-help/db/ora-01919/),
[ORA-01950](https://docs.oracle.com/en/error-help/db/ora-01950/),
[ORA-00955](https://docs.oracle.com/en/error-help/db/ora-00955/).
