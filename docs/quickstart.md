# Quickstart

From an empty machine to asking an AI assistant about your own training data, using a local
SQLite ledger. Plan on about 15 minutes, plus the time the first download takes.

For Oracle Autonomous Database instead of SQLite, follow the same steps but configure the
database as described in [Database](database.md) and [Oracle ADB setup](oracle-adb-setup.md).

## Prerequisites

- macOS or Linux with [Conda](https://docs.conda.io/) installed.
- A Garmin Connect account and a **Personal API key** created for your own account, following
  the [official Personal API instructions](https://developer.garmin.com/gc-developer-program/personal/personal-faq/).
  Your Garmin password is never used.
- An MCP client, for example a desktop AI assistant that supports local stdio MCP servers, or
  the [MCP Inspector](https://github.com/modelcontextprotocol/inspector) (needs Node.js).

## 1. Install

```sh
git clone https://github.com/luigisaetta/athlete-ledger.git
cd athlete-ledger
conda env create --file environment.yml
conda activate athlete-ledger
export UV_PROJECT_ENVIRONMENT="$CONDA_PREFIX"
uv sync --all-packages --inexact --locked
```

In every new shell, run `conda activate athlete-ledger` and export `UV_PROJECT_ENVIRONMENT`
again. This installs three commands: `athlete-ledger`, `athlete-ledger-mcp` and
`athlete-ledger-agent` (the last one is a placeholder for now).

## 2. Configure

Create a private `.env` in the repository root from the template. It is ignored by Git.

```sh
cp .env.example .env
```

For SQLite you need only two values:

```sh
GARMIN_PERSONAL_API_KEY=<your personal API key>
ATHLETE_LEDGER_DB_URL=sqlite:////absolute/path/to/athlete_ledger.db
```

Use an absolute path: the MCP server is started by your client from an arbitrary directory.
Leave the Oracle variables empty. Never put credentials on the command line.

## 3. Create the ledger

```sh
athlete-ledger db upgrade
athlete-ledger db check
```

`db check` should report the SQLite backend and the current schema revision.

## 4. Download your data

Start with a short range, then extend it as you like:

```sh
athlete-ledger sync backfill --since 2026-10-01
athlete-ledger sync coverage
```

`backfill` downloads activities, sleep, HRV and stress up to today. `coverage` shows which
periods are in the ledger and suggests the exact command to close any gap. From now on, keep the
ledger current with:

```sh
athlete-ledger sync run
```

The output contains only counts, never your data. Details: [Sync](sync.md).

## 5. Connect an MCP client

Find the absolute path of the server command:

```sh
command -v athlete-ledger-mcp
```

Add it to your client's MCP server configuration, pointing it at your `.env`:

```json
{
  "mcpServers": {
    "athlete-ledger": {
      "command": "/absolute/path/to/conda/envs/athlete-ledger/bin/athlete-ledger-mcp",
      "args": ["--env-file", "/absolute/path/to/athlete-ledger/.env"]
    }
  }
}
```

Restart the client and ask, for example:

- *"Which periods of my data are available?"*
- *"Show my weekly training load and time in heart-rate zones for the last month."*
- *"How did I sleep, and what was my HRV, in the days after my longest sessions?"*

To inspect the tools directly instead, run the MCP Inspector:

```sh
npx @modelcontextprotocol/inspector /absolute/path/to/athlete-ledger-mcp --env-file /absolute/path/to/athlete-ledger/.env
```

Before you connect a client, read the privacy notes in [MCP server](mcp-server.md#privacy):
tool results are sent to the model your client uses.
