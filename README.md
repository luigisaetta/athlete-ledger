# athlete-ledger

**Your training history, in a database you own, that any AI assistant can read.**

Your watch records every session, every night of sleep, and every HRV reading. Getting a useful
answer out of all that data is still hard. Dashboards show one chart at a time, and a general
purpose AI assistant cannot see your data at all.

athlete-ledger closes that gap. It builds a faithful, local archive of your own training and
wellness data, and serves it to AI assistants through the open
[Model Context Protocol](https://modelcontextprotocol.io) (MCP). Then you can simply ask:

> *"Show my weekly training load and time in heart-rate zones for the last two months."*
>
> *"How did I sleep, and what was my HRV, in the days after my longest rides?"*
>
> *"Which weeks this autumn had the most vigorous intensity minutes, and how did my stress look
> in those weeks?"*

The assistant calls a few small, read-only tools, gets back real numbers with explicit units,
and answers from your data, not from guesses.

## Goals

- **Official access, no password.** The first data provider is Garmin Connect, through the
  official [Personal API](https://developer.garmin.com/gc-developer-program/personal/personal-api)
  and Garmin's Python SDK. You use a personal API key; your account password is never involved.
- **A ledger you own.** Data is synced incrementally into your own database: SQLite on your
  laptop, or Oracle Autonomous Database. Your history stays available offline and does not depend
  on API quotas or key expiry. Every provider response is kept as received, so new fields can be
  added later without downloading again.
- **Standard, read-only AI access.** A standards-based MCP server exposes the ledger to any
  compatible assistant. Its tools only read, return data rather than opinions, and never contact
  the provider.
- **Privacy by design.** It runs on your machine, with no telemetry. Logs and command output
  never contain your data, and sensitive fields (GPS, location names, identity) are never
  exposed.
- **Any endurance sport.** Running, cycling, swimming, triathlon and more. The schema is ready
  for additional data providers.

## Status

> **Early development, but usable end to end:** you can download your data, keep it current, and
> query it from an MCP client today. Versions are `0.0.x`, nothing is published on PyPI yet, and
> interfaces and schema may still change.

| Area | State | What works today |
|---|---|---|
| Ledger store | ✅ Working | SQLite and Oracle ADB (thin-mode mTLS), versioned migrations, least-privilege accounts |
| Sync from Garmin Connect | ✅ Working | Activities, sleep, HRV and stress summaries: backfill, incremental sync, coverage and gap detection, rebuild from stored responses; automation-friendly with `--env-file`, `--json` output and a per-database lock |
| Activity training metrics | ✅ Working | Training effect and label, training load, TSS/IF/normalized power, elevation, max HR, speed, moving time, intensity minutes, time in HR zones |
| MCP server over stdio | ✅ Working | Four read-only tools with typed output schemas; verified on Oracle ADB with real data |
| MCP over Streamable HTTP | 🔜 Next | Stateless transport (MCP 2026-07-28), bound to localhost |
| Agent | 📋 Planned | Small agent that uses the MCP tools, working with any OpenAI-compatible Responses API (for example OCI Generative AI or OpenAI) |
| Chat UI | 📋 Planned | Next.js chat that shows every tool call |
| Packaging | 📋 Planned | Published packages, container setup, documentation for a first release |

Out of scope for now: activity files (FIT) and intraday series, derived training-load analytics
(CTL/ATL/TSB), writing back to Garmin, and multiple users.

## How it works

```
 Garmin Connect Personal API
            │  (official SDK, your personal API key)
            ▼
 athlete-ledger sync ──►  database you own (SQLite or Oracle ADB)
                          raw responses + normalized activity and daily tables
                                     │  read-only
                                     ▼
                          athlete-ledger-mcp  ──►  any MCP client
                                                   (desktop assistants, IDEs, MCP Inspector)
```

| Component | Command | Role |
|---|---|---|
| Core | `athlete-ledger` | Database schema and migrations, provider access, sync |
| MCP server | `athlete-ledger-mcp` | Read-only data tools over the ledger |
| Agent | `athlete-ledger-agent` | Planned; currently a placeholder |

The MCP tools are:

- `ledger_status`: what has been downloaded, with gaps;
- `list_activities`: activities with all their metrics;
- `activity_totals`: totals by week, month or sport;
- `daily_wellness`: sleep, HRV and stress, day by day.

## Documentation

| Guide | Read it to... |
|---|---|
| [Quickstart](docs/quickstart.md) | Go from zero to asking questions about your data, with SQLite |
| [Database](docs/database.md) | Configure SQLite or Oracle ADB, create and upgrade the schema |
| [Oracle ADB setup](docs/oracle-adb-setup.md) | Provision Autonomous Database step by step |
| [Sync](docs/sync.md) | Download, keep current, find gaps, rebuild |
| [MCP server](docs/mcp-server.md) | Tools, units, client configuration, privacy |
| [Development](docs/development.md) | Environment, checks, layout and conventions |
| [AGENTS.md](AGENTS.md) | Rules and phase specifications for contributors and coding agents |

## Privacy

Your data is health data, and athlete-ledger treats it that way. Everything runs locally, and
servers bind to `127.0.0.1`. There is no telemetry, and logs never contain your values. The MCP
tools are read-only; on Oracle this is enforced by a database account that can only read.

When you connect an AI assistant, your questions and the tool results are sent to the model that
assistant uses. Choose one whose data handling meets your requirements.

## Disclaimer

athlete-ledger is an independent open-source project. It is **not affiliated with, endorsed by,
or sponsored by Garmin**. Garmin and Garmin Connect are trademarks of Garmin Ltd. or its
subsidiaries. Use of the Personal API is subject to Garmin's terms; it is intended for individuals
accessing their own data.

## License

[MIT](LICENSE)
