# luzbarata

Tells people in Portugal the cheapest hours to use electricity tomorrow, from OMIE day-ahead
prices. A portfolio project for the owner (GitHub: nmmsousa) that should also earn some side
income: a free Telegram channel and web page first, then a cheap paid tier (personalised prices,
alerts, a Home Assistant API) once there is an audience. Keep anything that needs legal work
(personal data, payments) for later; the free version stores no user data.

See README.md for how the data works, the price formula and usage.

## Conventions

- Python 3.12, `uv` for everything: `uv run pytest`, `uv run ruff check . && uv run ruff format --check .`
- Run both before saying something is done, and run the real command against live data too
  (both phases found bugs only on live data or real fixtures).
- Tests use real OMIE files in `tests/fixtures/`. Add a fixture rather than inventing file contents.
- Never put a price, formula or schedule in the code or `tariffs.toml` without a primary source.
  Record the source URL and `checked_on`. Prices are EUR/kWh without VAT.
- Time: store UTC; OMIE delivery days are Europe/Madrid; show Europe/Lisbon. Compute durations
  from UTC datetimes (same-zone subtraction uses wall-clock time and breaks DST days).
- Commit messages: short imperative subject, bullet body.

## Things the code depends on (found by testing, not documented by OMIE)

- Unpublished days: OMIE redirects to a **404** (sometimes an HTML page with 200). `fetch` exits
  with code 2 for "not available yet", 1 for real failures.
- Tomorrow's prices appear in the early afternoon Lisbon time. Files are 15-minute since
  2025-10-01 (92/96/100 slots); history in `data/` starts at 2026-01-01.
- A Lisbon day spans two delivery days, so tomorrow 23:00-24:00 is unknown until the day after.

## Open items

- G9 Smart Index: their FAQ doesn't say whether TAR is added on top of the formula; we assume it
  is. Check against a real G9 bill or price table before publishing G9 prices.
- Losses use providers' indicative constants (15-16%), not ERSE's 15-minute loss profile
  (E-REDES publishes it yearly; file not found yet).
- TAR is only configured for 2026. Add 2027 values in January 2027.
- ERSE approved new time schedules: each BTN customer moves on a date between 2027-07-01 and
  2027-12-31. `tariffs.py` must support both, chosen per customer, before July 2027.

## Daily automation (phase 3)

- `.github/workflows/daily.yml`: fetch, post, commit `data/`. Channel tariffs are
  `CHANNEL_PROVIDER`/`CHANNEL_TARIFFS` in `cli.py`.
- Never post twice: `post` checks `data/telegram_posts.csv`, and the workflow checks out the
  branch tip (not the triggering commit) so a queued run sees the log the previous run pushed.
  Remaining gap: if posting succeeds but the push fails 3 times, the next run posts again.
- The last cron (`LAST_ATTEMPT`) turns "not available" into a failure, which is the
  evening alert. Keep the two in sync.
- Telegram errors must never include the request URL (it contains the bot token).

## Next: Phase 4 (web page with chart, JSON API, `.ics` feed)
