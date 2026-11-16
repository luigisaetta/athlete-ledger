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

The skeleton contains no provider, database, MCP server, or agent implementation yet. Planned
configuration names are listed with empty values in `.env.example`; settings loading starts in a
later phase. API references and verification gaps are in [docs/reference/README.md](docs/reference/README.md).
Contributors and coding agents: see [AGENTS.md](AGENTS.md).

Phase 0 requires local checks only.

## Disclaimer

athlete-ledger is an independent open-source project. It is **not affiliated with, endorsed by,
or sponsored by Garmin**. Garmin and Garmin Connect are trademarks of Garmin Ltd. or its
subsidiaries. Use of the Personal API is subject to Garmin's terms; it is intended for individuals
accessing their own data.

## License

[MIT](LICENSE)
