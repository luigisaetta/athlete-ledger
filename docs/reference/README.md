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

Phase 2 closes the runtime verification gap: the official `mcp==2.3.0` distribution is
installed and locked. See the source verification below for the serving and testing APIs.

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


## Personal API SDK source verification (Phase 1b)

Installed and locked version: `garmin-connect-personal-api-python==1.0.0`, imported as
`garmin_personal_api`. This review read the installed Python source (`api_keys.py`,
`_credentials.py`, `auth.py`, `client.py`, `errors.py`, `constants.py`, `__init__.py`). It did not
contact Garmin or read any real key, token cache, wallet or `.env`. Tests use synthetic settings
files inside temporary directories where required to verify settings loading.

Verified facts:

- `resolve_api_key` names its explicit argument **`api_key`**, not `key`. The application uses
  `resolve_api_key(api_key=...)`. Explicit values override discovery; with None, the SDK checks
  `GARMIN_PERSONAL_API_KEY` and then `~/.config/garmin/connect-personal-api-key`. It does not
  implicitly load the application's `.env`, so the settings model supplies the explicit secret.
- `PersonalApiAuthenticator.obtain_token(api_key, force_refresh=False)` returns `BearerToken`.
  The data-client constructor and `update_bearer_token` take **the access-token string**, not the
  token object. The adapter passes `BearerToken.access_token`.
- The default token cache is `~/.config/garmin/connect-personal-api-bearer-token.json`. Its
  fingerprint includes the key and base URL. The SDK reuses a token only when more than one hour
  remains before expiry; `force_refresh=True` bypasses cache reuse. New cache files use `0600`,
  but existing modes are not repaired by the SDK. The adapter tightens an existing cache to `0600`.
- The SDK issues one `requests` call per token/data operation, with no HTTP retry loop and no
  automatic refresh in the data client on 401. The application adds the bounded backoff and the
  single refresh/retry. Both SDK sessions have explicit 30-second request timeouts here.
- `HttpError` exposes `status_code`, `method`, `url`, `response_text`; its constructed message
  embeds the response text. The adapter never calls `str()` on it, reads only its status, and
  supplies verb/endpoint from fixed application mappings. A poisoned-`__str__` test verifies this.
- SDK log messages include request paths/parameters and can include credential-cache paths in
  warnings. CLI sync keeps the SDK logger at WARNING with a null handler and no propagation,
  using only application-controlled diagnostics instead.
- `search_activities` accepts keyword `start`, `limit`, `start_date`, `end_date`; it forwards one
  request and performs no paging. Sleep summaries send start/end as query parameters; HRV and
  stress summaries embed dates in the endpoint path. Wrapper return values are unmodified
  decoded JSON, or None for an empty body. The SDK does not validate envelopes or normalize
  sentinel values. The application stores the SDK call parameters so requests can be reproduced.

The response shapes and seven-day chunk policy follow `AGENTS.md` and are exercised with fakes;
reading these thin wrappers does **not** verify actual live response fields, API rate limits or
available historical depth. No new schema decision is needed for this phase. Runtime ORM writes
remain portable and compare raw JSON parameters in Python, avoiding Oracle CLOB equality.

Typing uses narrow injectable interfaces around the SDK and `types-requests` for strict checks.
The published SDK includes a `py.typed` marker, so no missing-import override is needed.
Application `src/` continues to pass `mypy --strict`. Google docstring rules are enabled. The exact required module
metadata layout has local `D205`/`D212` suppressions only at its closing quotes; function/class
rules remain enabled. A tracked-file header test checks author, date, MIT license and description.

Maintainer verification still required: real 14-day backfill on ADB, immediate repeat incremental
runs with zero updates when provider data is stable, and `sync status`. Confirm real pagination,
summary shapes, key discovery/token-cache behavior and permission mode on the maintainer's machine.
Report counts, date ranges and sanitized status codes only; do not attach raw payloads or secrets.

## MCP SDK source verification (Phase 2)

Installed versions: `mcp==2.3.0`, `mcp-types==2.3.0`. Reviewed the installed distribution's
README in METADATA and Python docstrings, `server/mcpserver/server.py`, `tools/base.py`,
`tools/tool_manager.py`, `utilities/func_metadata.py`, `utilities/logging.py`,
`client/client.py`, `client/_memory.py`, `client/stdio.py`, `shared/memory.py`,
`server/_otel.py` and `mcp_types/version.py` / `_types.py` before server implementation.

Verified API contracts:

- Import `MCPServer` from `mcp.server.mcpserver`; construct with `log_level="WARNING"`.
  Register functions via `server.tool(title=..., annotations=..., structured_output=True)`.
- `mcp.types.ToolAnnotations` uses Python snake-case `read_only_hint`, `idempotent_hint`,
  `destructive_hint` and `open_world_hint`; serialization publishes the protocol's camelCase
  names. Each tool may also have a title.
- Pydantic return annotations generate `outputSchema`. The SDK validates the result and emits
  `structuredContent` using JSON-mode model serialization, plus a text rendering for compatibility.
- Raise `mcp.server.mcpserver.exceptions.ToolError` for anticipated, sanitized failures. The
  SDK maps these to `isError` results. Pydantic argument errors are also tool errors; use
  input-hiding validation models and application-controlled diagnostics for private inputs.
- `server.run(transport="stdio")` runs the official stdio transport. It delegates protocol
  handling to the SDK, including modern per-request metadata and legacy client compatibility.
  The installed protocol registry includes `2026-07-28` as the latest modern version.
- `mcp.client.Client(server, mode="2026-07-28")` supports in-process tests without network;
  `Client(StdioServerParameters(...), mode="2026-07-28")` supports subprocess checks.
  Calls use `await client.list_tools()` and `await client.call_tool(name, arguments)`.
- SDK logging defaults can include exceptions and invalid request content. The application
  isolates dependency loggers and emits only controlled stderr diagnostics. SDK tracing uses
  `opentelemetry-api`; the CLI configures its public no-op tracer provider, with no exporter.

No HTTP transport, provider access or telemetry exporter is configured for Phase 2.
