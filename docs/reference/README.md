# External API references

Reviewed on 2026-11-16. These notes describe implementation constraints, not a claim that the
future integrations have been tested. Recheck the linked official sources before each relevant
phase. Phase 0 installs development tools only; runtime SDK versions will be selected and locked
when their features are introduced.

## MCP specification: 2026-07-28

Official sources:

- [Specification](https://modelcontextprotocol.io/specification/2026-07-28)
- [Key changes](https://modelcontextprotocol.io/specification/2026-07-28/changelog)
- [Release announcement](https://blog.modelcontextprotocol.io/posts/2026-07-28/)

The core uses independent JSON-RPC requests. This revision removes initialization and protocol
sessions, including `Mcp-Session-Id`. Requests carry protocol version and capabilities in `_meta`;
client identity also belongs there. Servers implement `server/discover`, which clients can call
optionally. Streamable HTTP requires routing headers `Mcp-Method` and `Mcp-Name`; disagreement with
the body produces error `-32020`. Results distinguish completed work from requests for further
input. List results have cache metadata and should have stable ordering. HTTP+SSE is deprecated;
use stdio and Streamable HTTP here. Streaming response events remain distinct from the deprecated
transport. Do not add telemetry just because the specification describes tracing conventions.

Project requirements: tools query only the database and declare `readOnlyHint`; annotations do
not themselves enforce read-only behavior. Local HTTP binds to `127.0.0.1`. Phase 3 must test
2026 requests and the separate compatibility path for 2025 clients.

## Official MCP Python SDK 2.x

Official sources:

- [Repository](https://github.com/modelcontextprotocol/python-sdk)
- [Migration guide](https://py.sdk.modelcontextprotocol.io/migration/)

Use the official `mcp` distribution, not the separate `fastmcp` package. The migration guide
confirms `from mcp.server.mcpserver import MCPServer`. Transport configuration has moved from the
server constructor to transport methods. Python attributes generally use snake case, while wire
serialization preserves protocol names; do not assume old handler signatures still work.

The SDK distinguishes 2026 stateless discovery from legacy initialization. Its migration guide
still describes legacy HTTP options such as `stateless_http=True`: that option alone does not
prove conformance to the new protocol. Use the documented compatibility facilities and test both
protocol eras rather than recreating the handshake or transport.

Verification gap: the guide and repository were reviewed, but no SDK runtime is installed or
exercised in Phase 0. Confirm a published 2.x release and its exact serving configuration before
Phase 2, then constrain the dependency and record the resolved version in `uv.lock`.

## OCI Responses API

Official source: [OCI Responses API](https://docs.oracle.com/en-us/iaas/Content/generative-ai/responses-api.htm).

The API accepts supported models through `/responses` and can stream output. Its base URL is
`https://inference.generativeai.${region}.oci.oraclecloud.com/openai/v1`. The documentation supports
using the `openai` Python SDK with OCI authentication. A compatible request format does not imply
that every model, region, or tool combination is supported.

Verification gap for Phase 4: current setup instructions require a Generative AI project, and the
example passes a project OCID through the client's `project` argument. The planned environment
variables now reserve `OCI_GENAI_PROJECT_ID`. Confirm SDK/project configuration before implementing
the agent in Phase 4. Credentials, regional availability, and real requests have not been tested.

## OCI compatible endpoints and tools

Official sources:

- [Compatible endpoints](https://docs.oracle.com/en-us/iaas/Content/generative-ai/openai-compatible-api.htm)
- [Supported tools](https://docs.oracle.com/en-us/iaas/Content/generative-ai/tool-support.htm)
- [Function calling examples](https://docs.oracle.com/en-us/iaas/Content/generative-ai/get-started-agents.htm)

OCI supplies its own credentials and regional inference endpoint. Check model and region support
before configuring the agent. The tool table includes `function` and `mcp`; their execution
models differ. With function calling, the application receives arguments, executes the function,
and returns its output for the next model response. The MVP follows this pattern: the local agent
is the MCP client and exposes database tools to the model as functions. OCI-hosted MCP calling
would contact a remote server directly and remains outside the MVP.

Verification gap: model availability and the maintainer's tenancy are untested. Phase 4 must
verify function schemas, call/result correlation, streaming events, and SDK/project compatibility
with synthetic data before real use.

## Garmin Connect Personal API

Official sources:

- [Personal API FAQ](https://developer.garmin.com/gc-developer-program/personal/personal-faq/)
- [Swagger documentation](https://developer.garmin.com/gc-developer-program/personal/swagger-docs/)
- [Python SDK](https://github.com/garmin/connect-personal-api-python)

The FAQ limits the API to access to an individual's own data. Exchange the personal API key for
an access token; the key is not the bearer token used for data requests. Tokens last up to 24
hours, bounded by key expiry, and there is no refresh token. Key expiry is selectable and must
be handled operationally. The SDK requires Python 3.12+ and documents key discovery through
`GARMIN_PERSONAL_API_KEY` or a key file. Keep all credentials outside source control.

Verification gap: the Swagger landing page was reachable, but its endpoint schema was not exposed
by the documentation reader. Endpoint date limits, payload shapes, pagination, status handling,
rate limits, and historical depth were not independently verified against Swagger. The Phase 1
rules in `AGENTS.md` remain requirements; check them against the SDK source and accessible schema
before implementing the provider adapter. No authenticated API call was made.

## Local environment tooling

Official sources:

- [uv project environment configuration](https://docs.astral.sh/uv/concepts/projects/config/)
- [uv locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/)

Conda owns the local Python environment `athlete-ledger`, using `conda-forge` and `nodefaults`
in `environment.yml`; uv owns the workspace dependency lockfile. Export
`UV_PROJECT_ENVIRONMENT="$CONDA_PREFIX"` after activation to select that prefix
explicitly. Sync all workspace members with `--inexact` so Conda bootstrap packages are preserved.
The `--active` option alone does not select this Conda environment. Shared local pre-commit hooks
use the installed environment tools instead of creating separate hook environments.

## Ledger storage: Oracle JSON and mTLS (Phase 1a)

Official sources:

- [SQLAlchemy 2.0 Oracle dialect](https://docs.sqlalchemy.org/en/20/dialects/oracle.html)
- [python-oracledb JSON support](https://python-oracledb.readthedocs.io/en/latest/user_guide/json_data_type.html)
- [Oracle IS JSON condition](https://docs.oracle.com/en/database/oracle/oracle-database/26/sqlrf/is-json-condition.html)
- [python-oracledb ADB connections](https://python-oracledb.readthedocs.io/en/latest/user_guide/connection_handling.html#connecting-to-autonomous-database)
- [Alembic offline migrations](https://alembic.sqlalchemy.org/en/latest/offline.html)
- [Oracle CREATE USER](https://docs.oracle.com/en/database/oracle/oracle-database/26/sqlrf/CREATE-USER.html)

Decision: `JSONDocument` uses text on SQLite and CLOB on Oracle, with named Oracle-only `IS JSON`
constraints on `raw_response.params` and `raw_response.payload`. `None` means SQL NULL; documents
must be dicts or lists. The driver supports native JSON, but that alone does not supply a
SQLAlchemy type or DDL compiler. The project's mandated SQLAlchemy 2.0 series is retained.

Evidence collected locally against locked SQLAlchemy **2.0.57** and python-oracledb **4.0.2**:

1. `hasattr(sqlalchemy.dialects.oracle, "JSON")` is false.
2. The Oracle type compiler has no `visit_JSON` method.
3. Compiling `CreateTable(Table("json_probe", MetaData(), Column("payload", JSON())))` with
   `oracle.dialect()` raises `CompileError`: the compiler cannot render type JSON.
4. The fallback generates `params CLOB`, `payload CLOB` and their `IS JSON` constraints in
   [the committed offline DDL](../../sql/oracle/schema.sql). SQLite migrations and document
   round-trips pass. The compiler probe and snapshot comparison are regression tests in
   `packages/core/tests/test_store.py`; run `pytest packages/core/tests/test_store.py` to reproduce.

Oracle documents `IS JSON` checks for CLOB columns. This supports the fallback design; it is
not evidence of a successful ADB execution. The custom result processor accepts textual JSON
and already-decoded documents to accommodate driver conversion. ADB must still verify that
behavior, large CLOB binding/fetching, Unicode, NULL and malformed document handling.

The documented thin-mode ADB connection supplies `user`, `password`, `dsn`, `config_dir`,
`wallet_location`, and `wallet_password`. The engine factory passes these explicitly, hides SQL
parameters, and never opens a connection at import time. Tests capture the connection arguments
without connecting. The wallet directory is shared by the two directory arguments.

Alembic's offline API renders the initial migration without credentials. Offline generation
suppresses implicit INSERT RETURNING so the revision insert does not require an output bind in
SQLcl; online migrations retain normal dialect behavior. Tests compare the generated script
byte-for-byte with the snapshot. Grants cover all six ledger tables and `alembic_version`;
downgrade revokes them before dropping tables. Migrations are frozen snapshots generated from
models, with a SQLite metadata-parity test guarding drift.

Unverified on ADB: bootstrap password prompts and policy, `DATA` quota, identity generation,
CLOB/JSON and UTC behavior, repeated upgrades, schema version reporting, and least-privilege
reader grants. Follow the README's Database checklist on the maintainer's own machine before
Phase 1b. Oracle DDL is not transactionally rolled back; inspect partially applied migrations
before retrying. The bootstrap preserves existing users' passwords and does not remove any
pre-existing grants.
