# ADR 0046 — A data space shared with the dilution analyzer

- **Status:** proposed. The scope is the user's decision (2026-11-02); the design waits for the reply of the "주식 희석 분석기" session, which owns that repository. Nothing has been moved.
- **Date:** 2026-11-02

## Context

The user wants SIW and `stock-dilution-analyzer` to use one data space, market data included. Both run on the same machine and today keep everything apart:

| Data | SIW | Dilution analyzer |
| --- | --- | --- |
| SEC filings | `public_sources.sqlite3`: parsed records with `first_observed_utc` (ADR 0024). Absent from `data/` so far | `.cache/sec`: 1.1 GB of raw responses, one file per URL |
| Splits and reverse splits | Alpaca adjustment, suspected split dates | `data/service/splits.sqlite`: read from filings, with `status` and `unresolved`, made for chart programs (its #85) |
| Ticker identity | `symbols/`, instrument ids such as `us:nvda:xnas:2026` | `tickers` table, by CIK |
| Dilution results | none | `data/service/results`, a versioned JSON contract |
| Market data | `metadata.sqlite3` (43 MB), `bars/` (17 MB), `ticks/` (6.5 MB) | none |

Three facts limit how this can be shared:

- **Opening the market data store writes.** `LocalMarketDataStore` takes an owner lock, runs migrations and recovers crashed writes when it opens (ADR 0002). A second program must not open it that way.
- **Both programs limit SEC requests alone.** SIW allows 5 per second and the analyzer under 10. Running together they can pass SEC's limit of 10.
- **The analyzer calculates nothing from prices.** That is its design decision D15. Prices go to the prototype in `~/Trading/dilution-analysis`, which reads its JSON.

## Decision

### One folder, one writer per dataset

`STOCK_SHARED_DATA` names a folder outside both repositories, proposed `~/Trading/stock-data`. It is on a local disk, never a network filesystem (ADR 0002). Unset, each program works alone as it does today, so tests and fresh installs keep their behaviour.

```text
$STOCK_SHARED_DATA/
  CONTRACT.json   the layout version, read by both programs
  sec/            raw SEC responses; both programs write
  sec.lock        the shared SEC request limit
  dilution/       splits.sqlite, results; the analyzer writes, SIW reads
  identity/       ticker to CIK to instrument id; SIW writes, the analyzer reads
  market/         metadata.sqlite3, bars, ticks; SIW writes, the analyzer reads
```

There is no single database. A schema change in one program must not be able to break the other, so each dataset has one owner and readers depend on a versioned layout.

These stay private to SIW: journal, paper orders, agents, backtests, indicators, credentials and backups. These stay private to the analyzer: the LLM cache, jobs and golden sets.

### SEC filings

Raw responses are shared. Immutable ones are safe for two writers because the same URL has the same body; writes go to a temporary file and are renamed. `sec.lock` holds one limit for both programs, 5 per second, using the existing fair-access rules.

SIW keeps its parsed records and revisions in its own `public_sources.sqlite3`. **`first_observed_utc` stays SIW's own**: the time SIW first read a record, even if the analyzer downloaded the file earlier. The file's own download time is kept apart, so a record is never claimed known before it was.

### Splits and dilution results

SIW reads `splits.sqlite` read-only. Only `status = confirmed` counts, and it is evidence shown next to Alpaca's adjustment: it does not change any price. If the two disagree the screen says so; choosing between them is a later decision.

### Market data

SIW remains the only program that fetches or writes bars. Keys, requests and cost stay in one place, and the analyzer holds no Alpaca key.

Readers never use `LocalMarketDataStore`. They open `metadata.sqlite3` read-only and read the parts its Coverage lists, under a reader contract that names the columns, the revision choice and the hash check. A range that is not covered is reported as unknown: a reader does not fetch and does not fill gaps.

The source stays on every bar and sources are never merged (ADR 0012). Having these bars readable does not change D15: whether the analyzer uses prices at all is decided on its side.

### Order of work

Each step ships and is checked alone, and a backup comes before any move.

1. The folder, `CONTRACT.json` and the two settings; the analyzer's data moves to `dilution/`; SIW reads confirmed splits.
2. Shared SEC responses and `sec.lock`: copy the 1.1 GB, confirm both programs read it, then remove the original.
3. Market data, **after the decision on `stock-intelligence-40`**. Cached 1-minute parts that disagree make `read_composed` fail today, and a second reader would inherit that failure.
4. `identity/`, the last because it needs a mapping design.

## Alternatives

- **One shared database:** the fewest files, but it ties the two migration histories together.
- **Only HTTP between the programs (ports 8765 and 8775):** no shared files and clean boundaries, but each needs the other running, and it leaves the duplicate SEC files. It stays available: the analyzer's `GET /api/v1/splits/{ticker}` already exists.
- **Keeping prices out, as first proposed:** less to design, but not what the user chose.

## Risks

- **Data rights:** Alpaca's terms have not been checked for a second program reading SIP data on the same machine. No data leaves the machine, but this is `unverified` and must be read before step 3.
- **Moving the market data store:** a mistake here loses cached bars and their Coverage. It can be fetched again, at the cost of new `first_observed` and revision values, so step 3 starts from a backup.
- **SIW Agent on Linux (ADR 0045):** the folder is per machine. The mini PC does not share it with this Mac, and nothing syncs them.
- **Locks:** `sec.lock` must be released by the kernel when a holder crashes, as the owner locks are.

## Reconsider when

- the programs move to different machines;
- a third program needs this data, which would justify a service instead of a folder;
- readers need bars that SIW has not cached, which raises who fetches them;
- the layout changes often enough that both programs keep breaking on `CONTRACT.json`.
