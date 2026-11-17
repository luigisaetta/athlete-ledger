# Development

## Environment

The project uses Python 3.12+, [Conda](https://docs.conda.io/) for the local environment and
[uv](https://docs.astral.sh/uv/) for the Python workspace and lockfile. From the repository root:

```sh
conda env create --file environment.yml
conda activate athlete-ledger
export UV_PROJECT_ENVIRONMENT="$CONDA_PREFIX"
uv sync --all-packages --inexact --locked
pre-commit install
```

If the environment already exists, start at `conda activate athlete-ledger`, and export
`UV_PROJECT_ENVIRONMENT` in every new shell. `--inexact` preserves the Conda-managed bootstrap
packages. `environment.yml` defines Python and uv; `uv.lock` pins every workspace dependency.
The pre-commit hooks use the tools installed in this environment, so keep it active when
committing.

## Acceptance checks

Every change must pass, locally:

```sh
uv run --all-packages --no-sync ruff check .
uv run --all-packages --no-sync ruff format --check .
uv run --all-packages --no-sync mypy
uv run --all-packages --no-sync pytest
```

Tests use synthetic data only (SQLite, fake provider clients, in-process MCP clients) and never
touch the network or real credentials. Checks against Oracle ADB and real data are run manually
by the maintainer. Continuous integration is not enabled yet.

## Repository layout

```
packages/
  core/        athlete-ledger: settings, database, migrations, provider access, sync CLI
  mcp-server/  athlete-ledger-mcp: read-only MCP server over stdio
  agent/       athlete-ledger-agent: placeholder for the planned agent
docs/          user documentation; docs/reference/ holds verified notes on external APIs
sql/oracle/    bootstrap script and generated DDL
scripts/       helper scripts (create_tables.py)
```

## Conventions

The full rules for contributors and coding agents are in [AGENTS.md](../AGENTS.md). In short:

- no real personal data or credentials anywhere; no health data in logs;
- MCP tools are read-only and read the database only;
- servers bind to `127.0.0.1`; no telemetry;
- every Python file starts with the standard header and uses Google-style docstrings;
- schema changes go through a new Alembic migration and a regenerated `sql/oracle/schema.sql`;
- Conventional Commits, one phase or patch per branch and pull request.

External API behaviour (MCP specification and SDK, Garmin SDK, Oracle storage choices) is
summarized, with links to the sources, in [docs/reference](reference/README.md).
