# Sync

The `athlete-ledger sync` commands download your data from Garmin Connect into the ledger and
keep it current. Sync is the **only** part of athlete-ledger that talks to the provider; the MCP
server reads the database only.

MVP data areas, summaries only: **activities**, **sleep**, **HRV** and **stress**.

## Credentials

Create a Personal API key for your own account using the
[official Personal API instructions](https://developer.garmin.com/gc-developer-program/personal/personal-faq/).
Set it privately as `GARMIN_PERSONAL_API_KEY` in your environment or local `.env`. If it is not
set, the SDK falls back to its default key file `~/.config/garmin/connect-personal-api-key`
(protect it with mode `0600`). Never put credentials on the command line. Keys expire (between
30 days and one year): an expired key produces an HTTP 401/403 error.

The SDK caches bearer tokens in `~/.config/garmin/connect-personal-api-bearer-token.json`,
outside the repository, with mode `0600`. Tokens last 24 hours; on a 401 the ledger obtains a
new token once and retries.

## Commands

```sh
athlete-ledger sync backfill --since 2026-01-01 [--until 2026-01-31] [--dataset NAME ...]
athlete-ledger sync run      [--dataset NAME ...]
athlete-ledger sync legs     [--refresh]
athlete-ledger sync status
athlete-ledger sync coverage [--dataset NAME ...]
athlete-ledger sync rebuild  [--dataset NAME ...]
```

`NAME` is `activities`, `sleep`, `hrv` or `stress`; by default all four run. Rebuild also
accepts `legs` and includes it by default, after activities. `backfill`, `run` and `legs`
contact the provider. `status`, `coverage` and `rebuild` read the database only and never
look for the API key.

| Command | What it does |
|---|---|
| `backfill` | Downloads an explicit historical range. `--until` defaults to today; future dates and reversed ranges are rejected. |
| `run` | Incremental sync: from the last synced date minus two days through today, so the last three days are always refreshed (the provider recalculates recent data). Needs a previous backfill. |
| `status` | Last synced date, last run time and status per dataset, plus the stored leg count. |
| `coverage` | Downloaded periods, gaps, and days not yet synced, per dataset, with the `backfill` command that closes each gap. |
| `legs` | Fetches details for multisport parents not yet examined; `--refresh` fetches all stored multisport parents again. |
| `rebuild` | Re-derives the normalized tables from stored raw responses, with no download. |

Run only one sync or rebuild process at a time for a database.

## How a download works

- Requests use inclusive windows of at most seven days. Activities are paged until an empty page;
  pagination that makes no progress is an error.
- Every response is stored in `raw_response` first, then normalized. A response identical to the
  last one stored for the same request is skipped, so the ledger does not grow on repeated runs.
- Normalized rows are updated only when a value changes. For repeated records the **last applied
  occurrence wins**, both during sync and during rebuild.
- Each seven-day window is committed with its raw responses, normalized rows and progress, so an
  interrupted run keeps the completed windows and resumes cleanly. A failed window is rolled back;
  other datasets continue, and the command exits non-zero.
- Records that disappear from later provider responses are **not deleted**: provider-side
  deletions are outside the MVP.
- Transient errors (HTTP 429, 5xx, timeouts, connection errors) are retried up to three times,
  waiting 2, 4 and 8 seconds.

Value rules: missing, non-numeric and negative values (the provider's sentinels) become NULL;
zero is a valid value; daily records without a date are placeholders and are skipped. Training
effects outside 0–5 become NULL. Activity start times are kept as the provider's local wall time.

## Multisport legs

A multisport activity appears in the activity list as one summary with the totals of all its
legs. The list has no leg identifiers. `sync legs` downloads the parent detail to read its
ordered `childIds`, then each leg detail: **1 + N requests per multisport parent**, before any
retries. Transitions are legs too. The rows go to the separate `activity_leg` table; the parent
stays in `activity`, so activity totals are unchanged and never count the legs twice.

```sh
athlete-ledger sync legs
athlete-ledger sync legs --refresh
```

Without `--refresh`, only stored `multi_sport` parents with no stored parent-detail raw response
are selected. A successfully checked parent with zero legs is not fetched again automatically.
Use `--refresh` to revisit all parents, including those already checked. `backfill` and `run`
also run this step after the activities dataset when activities is selected; the step checks
all stored eligible parents, independently of the download's date range.

Each parent and all its legs commit in one transaction. A malformed detail or failed request
rolls back that family only; other parents continue and the command exits non-zero. An initial
failure leaves the parent eligible for the next run. A failed refresh keeps the previous family;
retry it with `--refresh`. Missing legs in later responses are not deleted.

Details are stored in `raw_response` under `get_activity`, with the parent's local date as both
range boundaries. They do not form a dataset, do not create `sync_state` rows, and never count
as downloaded coverage. Normalized legs contain only the specified metrics, identifiers and
order; names, GPS, location, owner and device fields stay only in raw responses.

## Coverage

`sync status` shows only how far each dataset has progressed, which cannot reveal a hole between
an older and a newer download. `sync coverage` does: it merges the date ranges actually downloaded
(read from `raw_response`) and prints, per dataset, the covered intervals, each internal gap, and
the tail from the last downloaded day to today. For each gap it prints the exact command to fill
it, for example:

```sh
athlete-ledger sync backfill --since 2026-01-08 --until 2026-01-09 --dataset sleep
```

Coverage describes downloaded date ranges, not the number of records: a day without activities
or a missing daily summary can still belong to a downloaded interval.

## Rebuild from stored responses

When a new version maps more provider fields (for example the activity training metrics added in
schema revision `0002_activity_training_metrics`), upgrade the database and then run:

```sh
athlete-ledger sync rebuild
```

Rebuild replays every stored non-empty response in fetch order through the same logic used by
sync. It uses the owner connection, needs no API key, makes no downloads, never modifies
`raw_response` or `sync_state`, and never deletes rows. It runs one transaction per dataset. A
second rebuild with unchanged data reports zero inserts and zero updates.

The default rebuild also re-derives legs from stored `get_activity` responses after activities.
Parent details supply the order and leg details supply the metric values; later stored details
win. Legs use one transaction, with endpoint and raw row ID only on failure. To replay only legs:

```sh
athlete-ledger sync rebuild --dataset legs
```

Rebuild does not download missing details: use `sync legs` first when they have never been fetched.

## Output and privacy

Output contains only counts per dataset (windows, requests, raw responses stored or unchanged,
rows inserted, updated or unchanged, placeholders skipped), dates and safe error messages
(endpoint, HTTP status, raw row id). Metric values, activity names, payloads, keys, tokens and
provider error bodies are never printed or logged. Leg sync prints only parents checked,
detail requests, legs inserted/updated/unchanged and parents failed; unexpected detail failures
name only `get_activity`.

Tests use fake clients and synthetic data only, with guards against real network calls. SDK
behaviour verified from its source is recorded in the
[reference notes](reference/README.md#personal-api-sdk-source-verification-phase-1b).
