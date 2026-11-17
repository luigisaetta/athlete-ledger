# MCP server

`athlete-ledger-mcp` exposes the ledger to any client that speaks the
[Model Context Protocol](https://modelcontextprotocol.io) (MCP). It is built on the official MCP
Python SDK 2.x and targets the **2026-07-28** specification, with compatibility for older
clients handled by the SDK.

Design rules:

- **Read-only.** Four tools, all annotated as read-only, idempotent and closed-world. On Oracle
  the server connects only as the read-only user; SQLite is opened read-only.
- **Database only.** The server never contacts the provider and never reads the API key.
- **Data, not analysis.** Tools return values with explicit units and plain sums. Interpretation
  is left to the assistant.
- **Local.** Transport is stdio only, at the moment; there is no network listener and no telemetry.

## Tools

| Tool | Returns |
|---|---|
| `ledger_status()` | Today's local date; per dataset, downloaded intervals and gaps, sync progress and first/last record dates; the sports in the ledger with activity counts. Clients should call it first. |
| `list_activities(start, end, sport=None, limit=50)` | Activities in a date range, in chronological order (limit 1–200), with total matches, truncation flag and download coverage. |
| `activity_totals(start, end, group_by)` | Totals grouped by ISO week, month or sport, plus overall totals, counts of missing values and download coverage. |
| `daily_wellness(start, end, areas=None)` | One row per day (at most 92 days) with sleep, HRV and stress values and a state per area. |

Dates are `YYYY-MM-DD`, inclusive at both ends. Activity times are the provider's local wall time,
without a timezone. `null` means unavailable; zero is a valid value. Every result has a declared
output schema, with units in field names and descriptions.

### Activity fields

| Field | Unit |
|---|---|
| `distance_km`, `duration_min`, `moving_duration_min` | km; minutes |
| `avg_hr_bpm`, `max_hr_bpm`, `calories_kcal` | beats/minute; kcal |
| `avg_speed_kmh`, `elevation_gain_m`, `elevation_loss_m` | km/h; metres |
| `training.aerobic_effect`, `training.anaerobic_effect`, `training.effect_label` | provider values, 0–5; provider label |
| `training.load`, `training.stress_score`, `training.intensity_factor`, `training.normalized_power_w` | provider load units; TSS points; ratio; watts |
| `intensity_minutes.moderate`, `intensity_minutes.vigorous` | minutes |
| `hr_zone_min.zone_1` … `zone_5` | minutes in each heart-rate zone |

TSS, IF and normalized power are available mostly for activities recorded with a power meter.
Nested objects are always present, with `null` members when values are missing.

#### Multisport legs

Every activity returned by `list_activities` includes `legs`, ordered by the zero-based
`leg_index` from the parent's `childIds`. Transitions appear as legs with sport `transition_v2`.
Non-multisport activities and multisport parents without stored legs have `legs: []`. Download
the details first with `athlete-ledger sync legs`; the MCP server reads only stored data.

| Leg field | Unit and rounding |
|---|---|
| `activity_id`, `leg_index`, `sport` | Provider identifier; zero-based order; provider sport text |
| `start_time_local` | ISO local wall time, without a timezone; `null` if missing |
| `distance_km` | km, 2 decimals |
| `duration_min`, `moving_duration_min` | minutes, 1 decimal |
| `avg_hr_bpm`, `max_hr_bpm` | beats/minute, 1 decimal |
| `calories_kcal` | kcal, as stored |
| `avg_speed_kmh` | km/h (stored m/s × 3.6), 1 decimal |
| `elevation_gain_m`, `elevation_loss_m` | metres, 1 decimal |
| `training.aerobic_effect`, `training.anaerobic_effect` | Provider effects, 0–5, 1 decimal |
| `training.effect_label`, `training.load`, `training.normalized_power_w` | Provider label; provider load units, 1 decimal; watts, 0 decimals |
| `intensity_minutes.moderate`, `intensity_minutes.vigorous` | Integer minutes |

Leg `training` and `intensity_minutes` objects are always present, with `null` members for
missing values. Legs never expose names, even when activity names are enabled; TSS, IF and
heart-rate zones are absent from legs. Parent sport filters, limits, match counts and truncation
are unaffected by legs. Totals count each multisport activity once, using the parent's totals
for all its legs, so no leg is counted twice.

### Totals

Each group and the overall total contain the activity count and plain sums of distance, duration,
moving time, calories, elevation gain, training load, TSS, intensity minutes and time per HR zone,
plus the duration-weighted average HR. Sums skip nulls; an empty or all-null sum is `null`, and a
`missing` object counts the activities lacking each value. There are no averages of training
effect or intensity factor and no derived scores.

### Wellness states

For each day and area, `daily_wellness` reports `not_downloaded` (outside the downloaded
coverage), `no_record` (downloaded, no data for that day) or `ok` (values present, individual
metrics may still be `null`). This keeps "no data downloaded" distinct from "nothing recorded".

## Configuration

The server reads its settings from `--env-file PATH` (default: `.env` in the current directory)
and from environment variables. Desktop clients start it from an arbitrary directory, so always
pass an absolute `--env-file` and use absolute paths inside it.

- **SQLite**: `ATHLETE_LEDGER_DB_URL` must point to an existing ledger file; it is opened
  read-only and never created.
- **Oracle**: set `ATHLETE_LEDGER_RO_DB_USER` and `ATHLETE_LEDGER_RO_DB_PASSWORD` for the
  read-only user, plus the DSN and wallet variables described in [Database](database.md). Owner
  credentials are ignored and never used as a fallback. Each connection sets `CURRENT_SCHEMA` to
  `ATHLETE_LEDGER_DB_SCHEMA` (default `ATHLETE_LEDGER`).
- `ATHLETE_LEDGER_MCP_INCLUDE_ACTIVITY_NAMES=true` adds activity names to results (default:
  excluded, see [Privacy](#privacy)).

## Connect a client

Find the absolute path of the command with `command -v athlete-ledger-mcp`, then add it to the
client's MCP configuration. Keep secrets in the env file, not in the client configuration:

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

To inspect the tools and their schemas, run the official
[MCP Inspector](https://github.com/modelcontextprotocol/inspector) (requires Node.js):

```sh
npx @modelcontextprotocol/inspector /absolute/path/to/athlete-ledger-mcp --env-file /absolute/private/path/ledger.env
```

stdout carries only MCP messages, so the server prints no banner. Diagnostics go to stderr and
are sanitized: configuration problems name only the variables involved, database errors show only
an `ORA-`/`DPY-` code, and no SQL, values, credentials or tracebacks are logged.

## Privacy

Tool results are sent to whatever model your MCP client uses. Choose a client and model whose
data handling meets your requirements.

- Activity names can contain places or people, so they are excluded unless you enable them.
- GPS coordinates, location names, owner identity and device fields are never exposed.
- Metric values are, by design, part of the results you ask for.
