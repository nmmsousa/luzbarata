# luzbarata ⚡

Tells people in Portugal the cheapest hours to use electricity tomorrow, based on the
OMIE day-ahead market prices that are published every afternoon.

> Status: **Phase 1** – price ingestion. Cheapest-window logic, Telegram channel and web page
> come next (see [Roadmap](#roadmap)).

## How the data works

OMIE (the Iberian electricity market operator) publishes a file per delivery day with the
marginal price for Portugal (`marginalpdbcpt_YYYYMMDD.1`). A few things make it less simple
than it looks, and the ingestion handles all of them:

| Quirk | How it is handled |
|---|---|
| Delivery days and period numbers are in **Spanish time** (Europe/Madrid), so period 1 starts at 23:00 Lisbon time the previous day | Periods are stored with exact `start_utc`/`end_utc`; display converts to Europe/Lisbon |
| **Hourly** periods until 2025-09-30, **15-minute** periods from 2025-10-01 | Resolution is derived from the length of the day and the row count, then validated |
| **Daylight saving** days are 23h or 25h long (23/25 or 92/100 periods) | Day length comes from Madrid timezone rules; any other count is rejected |
| Days not yet published return a **404** (or sometimes an HTML page with HTTP 200) | Detected and reported as "not available" (exit code 2) without retrying, separate from real failures |
| Network hiccups | Retries with backoff on HTTP errors |

Validation also rejects rows for another day, duplicate or missing periods, and prices
outside the harmonised market limits (-500 to 4000 EUR/MWh).

## Storage

Parquet, one file per month, rewritten atomically:

```
data/prices/year=2026/2026-10.parquet
```

| column | type | notes |
|---|---|---|
| delivery_date | date | market day (Madrid) |
| period | int16 | 1..n |
| resolution_minutes | int16 | 60 or 15 |
| start_utc / end_utc | timestamp UTC | exact period bounds |
| price_eur_mwh | float64 | Portuguese marginal price |
| ingested_at_utc | timestamp UTC | when it was downloaded |

Saving a day replaces any rows already stored for it, so every command is safe to re-run.
Query it with DuckDB: `select * from 'data/prices/*/*.parquet'`.

## Usage

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync

uv run luzbarata fetch                      # tomorrow (exit 2 if not published yet)
uv run luzbarata fetch --date 2026-10-26
uv run luzbarata backfill 2024-01-01 2026-10-26   # skips days already stored, 1s between requests
uv run luzbarata show --date 2026-10-26     # prices in Lisbon time
```

The store location defaults to `./data`; override with `--data-dir` or `LUZBARATA_DATA_DIR`.

## Development

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Tests run against real OMIE files saved in `tests/fixtures/`, covering every combination of
hourly / 15-minute and normal / spring-forward / fall-back days.

## Roadmap

1. ✅ Ingestion: fetch, validate, store, backfill
2. Cheapest windows (1h / 2h / 4h) and final price for 2–3 indexed-tariff providers
3. GitHub Actions daily schedule + Telegram channel message
4. Web page with chart, JSON API and `.ics` calendar feed
5. Personalisation (provider, tariff schedule), alerts, savings calculator

*Prices shown are estimates for information only. Market data © OMIE.*
