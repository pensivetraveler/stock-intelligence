# ADR 0046 — A data space shared with the dilution analyzer

- **Status:** proposed. The scope is the user's decision (2026-11-02). This version takes in the reply of the "주식 희석 분석기" session, which owns that repository (it read its own code at f56cb35 and ran nothing). Nothing has been moved.
- **Date:** 2026-11-02

## Context

The user wants SIW and `stock-dilution-analyzer` to use one data space, market data included. Both run on the same machine and today keep everything apart:

| Data                      | SIW                                                                                                       | Dilution analyzer                                                                                                  |
| ------------------------- | --------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| SEC filings               | `public_sources.sqlite3`: parsed records with `first_observed_utc` (ADR 0024). Absent from `data/` so far | `.cache/sec`: 1.1 GB of raw responses, one file per URL                                                            |
| Splits and reverse splits | Alpaca adjustment, suspected split dates                                                                  | `data/service/splits.sqlite`: read from filings, with `status` and `unresolved`, made for chart programs (its #85) |
| Ticker identity           | `symbols/`, instrument ids such as `us:nvda:xnas:2026`                                                    | `tickers` table, by CIK                                                                                            |
| Dilution results          | none                                                                                                      | `data/service/results`, a versioned JSON contract                                                                  |
| Market data               | `metadata.sqlite3` (43 MB), `bars/` (17 MB), `ticks/` (6.5 MB)                                            | none                                                                                                               |

Three facts limit how this can be shared:

- **Opening the market data store writes.** `LocalMarketDataStore` takes an owner lock, runs migrations and recovers crashed writes when it opens (ADR 0002). A second program must not open it that way.
- **The two programs limit SEC requests apart.** SIW allows 5 per second, in one process. The analyzer allows about 6.7, across its processes. Running together they can pass SEC's limit of 10.
- **The analyzer trusts its cache.** A file under `Archives` is believed forever, with no hash check, and is not part of its analysis key. A bad file written there would change results silently.
- **The analyzer calculates nothing from prices.** That is its design decision D15, and it has no plan to read bars. Prices go to the prototype in `~/Trading/dilution-analysis`, which reads its JSON.

## Decision

### One folder, one writer per dataset

`STOCK_SHARED_DATA` names a folder outside both repositories, proposed `~/Trading/stock-data`. It is on a local disk, never a network filesystem (ADR 0002). Unset, each program works alone as it does today, so tests and fresh installs keep their behaviour.

```text
$STOCK_SHARED_DATA/
  CONTRACT.json   the layout version, read by both programs
  sec/            raw SEC responses; both programs write
  sec.lock        the shared SEC request limit
  dilution/       the analyzer's whole service folder; SIW reads only splits.sqlite
  siw/            exports from SIW, such as split disagreements; SIW writes
  identity/       ticker to CIK to instrument id; SIW writes, the analyzer reads
  market/         metadata.sqlite3, bars, ticks; SIW writes, no reader yet
```

There is no single database. A schema change in one program must not be able to break the other, so each dataset has one owner and readers depend on a versioned layout.

These stay private to SIW: journal, paper orders, agents, backtests, indicators, credentials and backups. The analyzer's golden sets and LLM answer cache outside its service folder stay private to it.

### SEC filings

Raw responses are shared, under the analyzer's existing layout: `<host>/<sha256(url)[:16]>-<last path segment>`, holding the decoded response bytes with no wrapper. A URL that differs by one character is a different file, so SIW must request the same URL strings to share anything.

Both writers keep these rules, because a bad file in `Archives` would not be noticed:

- **Only complete responses:** the raw bytes of a complete 200 response, written to a temporary file and renamed. Never an error body, a 404, a truncated body or re-serialized content.
- **Immutable files:** everything under `Archives`, `index.json` included. An existing one is never downloaded again or overwritten.
- **Mutable files:** the ticker list, submissions (older pages included) and companyfacts. Their age is the file's mtime, 24 hours for the analyzer. A file's mtime changes only when it is really downloaded again, and copies keep it (`rsync -a`).

**The request limit is the analyzer's existing lock, not a new protocol.** `SEC_RATE_LOCK` points at `sec.lock`. A process holds `flock` while it waits and until its response ends. The file holds the last request's `CLOCK_MONOTONIC` time, or a time far in the future during a send, so a holder that crashes still leaves one interval. SIW adopts this in place of its in-process limiter.

Each program computes the wait from its own interval, so both must use 0.2 seconds or the total of 5 per second breaks. The analyzer will change from 0.15. The lock has no queue, so a long SIW download must pause between requests, or an analyzer job can run out of its time budget. That makes the job incomplete, not wrong.

SIW keeps its parsed records and revisions in its own `public_sources.sqlite3`. **`first_observed_utc` stays SIW's own**: the time SIW first read a record, even if the analyzer downloaded the file earlier. The shared folder does not record a separate download time, and the mtime is no such record, so nothing may treat a file's time as proof of when a record was known.

### Splits and dilution results

The analyzer's `DILUTION_SERVICE_DATA` decides every path of its service together, and the index of its results is in `jobs.sqlite`, so the whole folder moves to `dilution/`. SIW reads only `splits.sqlite`. Results, if SIW wants them, come from `GET /api/v1/results/{id}`, which knows the latest edition.

SIW's reader is one function, so it can move to HTTP later, and it keeps to these rules:

- **Not checked is not none:** find the CIK through `tickers`, then read `splits`. No `issuers` row means not checked; a row with no splits means none found.
- **The period read:** before `issuers.filings_from` (5 years by default) is also not checked.
- **Short reads:** the database uses journal mode `delete`, so a long read blocks the analyzer's writes. Read briefly, and do not use `immutable=1`.
- **The layout version:** the file has none today (`user_version` is 0). The analyzer adds one in step 1, and SIW refuses a version it does not know.

Only `status = confirmed` counts, and it is evidence shown next to Alpaca's adjustment: it does not change any price. These values are read from filings by rules and have not been compared with another source, some are wrong, and a real reverse split can sit in `conflict` (CDT 1/100). The screen labels them as filing readings, not cross-checked, and when the two differ it says so without calling either wrong. SIW exports that list to `siw/split-disagreements.json`, the input to the analyzer's own check of its values.

### Market data

SIW remains the only program that fetches or writes bars. Keys, requests and cost stay in one place, and the analyzer holds no Alpaca key.

**There is no reader today.** The analyzer will not read bars while D15 stands, and changing D15 is the user's decision. So the reader contract is written when a first reader is named, not before. The prototype in `~/Trading/dilution-analysis` is the likely one; it has not been asked.

That contract will keep to this: readers never use `LocalMarketDataStore`, they open `metadata.sqlite3` read-only and read the parts its Coverage lists, and a range not covered is reported as unknown. The source stays on every bar and sources are never merged (ADR 0012).

### Order of work

Each step ships and is checked alone, and a backup comes before any move.

1. **Done (2026-11-02).** The analyzer moved `data/service` to `dilution/` and set `user_version` to 1. It finds `DILUTION_SERVICE_DATA` in the environment, then in `.env`, then falls back to its repository, so a run that forgets the setting does not start an empty folder. SIW reads confirmed splits (below).
2. Shared SEC responses and the lock. The analyzer needs a setting for its cache path, fixed today, and the 0.2 second interval. Copy `www.sec.gov` (1.1 GB) and `data.sec.gov` (55 MB) keeping mtimes, confirm both programs read them, then remove the originals. The 465 old-format files at the top of `.cache/sec` are not read by current code and stay behind.
3. Market data moves to `market/`, **after the decision on `stock-intelligence-40`**. Cached 1-minute parts that disagree make `read_composed` fail today, and a reader would inherit that failure.
4. `identity/`, the last because it needs a mapping design.

## Alternatives

- **One shared database:** the fewest files, but it ties the two migration histories together.
- **Only HTTP between the programs (ports 8765 and 8775):** no shared files and clean boundaries, but each needs the other running, and it leaves the duplicate SEC files. It stays available: the analyzer's `GET /api/v1/splits/{ticker}` already exists.
- **Keeping prices out, as first proposed:** less to design, but not what the user chose.

## Risks

- **Data rights:** Alpaca's terms have not been checked for a second program reading SIP data on the same machine. No data leaves the machine, but this is `unverified` and must be read before step 3.
- **Moving the market data store:** a mistake here loses cached bars and their Coverage. It can be fetched again, at the cost of new `first_observed` and revision values, so step 3 starts from a backup.
- **SIW Agent on Linux (ADR 0045):** the folder is per machine. The mini PC does not share it with this Mac, and nothing syncs them.
- **The analyzer's move to AWS:** this is planned (its S10–S12), not a distant idea. Sharing `dilution/` and `sec/` ends then and only HTTP remains, which is why SIW's splits reader is one function.
- **SEC files changing during an analysis:** when one run reads a URL several times, only the last content enters the analyzer's key. The gap exists today, and frequent SIW refreshes make it more likely for a run dated today. Its size has not been measured.
- **Rights and need for `market/`:** with no reader, moving the store gains nothing yet and still carries the move's risk.

## Reconsider when

- the analyzer moves to AWS, or the programs otherwise run on different machines;
- a third program needs this data, which would justify a service instead of a folder;
- readers need bars that SIW has not cached, which raises who fetches them;
- the layout changes often enough that both programs keep breaking on `CONTRACT.json`.

## Step 1 in SIW (2026-11-02)

- **Setting:** `STOCK_SHARED_DATA` in `.env`. Unset, the API answers `unavailable` and nothing is read.
- **Reader:** `read_filing_splits` in `services/market_data/filing_splits.py` is the only code that opens the file. It opens it with `mode=ro`, refuses any `user_version` but 1, and keeps not checked, none found and found apart.
- **API:** `GET /v1/market/filing-splits?instrument_id=`. Alpaca's splits (`/v1/corporate-actions`, by `ex_date`) are asked for only when the analyzer has rows, and kept in memory for 6 hours.
- **Chart:** US daily and weekly charts show a note labelled as filing readings, not cross-checked. A replay does not ask, because the readings name future splits. No price, backtest or Agent uses them.
- **Comparison:** only inside the period the analyzer read. A filing's effective date and Alpaca's ex-date count as one split within 3 calendar days and the same ratio. That margin is a judgement from CDT's 2025-05-19 and 2025-05-20; it is not a rule from either source.
- **Differences:** `ratio_differs`, `filing_only`, `provider_only`, and `filing_unconfirmed` when Alpaca's split matches a reading the analyzer left in `conflict` or `scheduled`.
- **Export:** `uv run --env-file .env python -m services.market_data.filing_splits export` writes `siw/split-disagreements.json` and `CONTRACT.json`. It runs by hand; nothing schedules it.
- **Limits:** a renamed ticker is asked of Alpaca only under its current symbol. Delisted instruments use their ticker part as is, so a reused ticker could match another issuer's readings.
