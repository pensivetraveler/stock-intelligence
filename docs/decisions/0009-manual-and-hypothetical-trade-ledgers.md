# ADR 0009 — Manual (actual) and hypothetical trade ledgers

- **Status:** accepted
- **Date:** 2026-10-16

## Context

Phase 3 starts trade review. The workstation must keep four kinds of fill apart: broker-confirmed, user-entered actual, hypothetical and backtest. It must never treat a clicked candle as the fill time or price. It stores amounts as decimals and never mixes actual and simulated P&L.

The 0.1 `manual_execution` and `scenario_definition` contracts were drafts:

- `manual_execution` had no field for the user-claimed fill time, and no fees or memo;
- `scenario_definition` had no direction and no entry/exit anchors.

No records of either kind had been stored yet.

## Decision

- **Contract changes:** these are required fields in the unreleased 0.1 draft, and the fixtures were updated with them.
  - `manual_execution` gains:
    - `executed_at_utc`, which is null exactly when `time_precision` is `unknown` and can never be later than `asserted_at_utc`;
    - `fees` and `memo`.
  - `scenario_definition` gains `direction` (long/short), `entry_interval_start_utc`, `exit_interval_start_utc` and `created_at_utc`. The exit must follow the entry.
  - Quantities and prices must be positive in both.
  - New `manual_ledger_response` and `scenario_ledger_response` contracts are added.
- **Storage:** a separate SQLite file, `journal.sqlite3` in `SIW_DATA_DIR`, with its own migrations.
  - Actual and hypothetical records live in separate tables and are append-only.
  - The client sends a record ID. Repeating the same request returns the stored record; reusing the ID with different content returns 409 and never overwrites.
  - Without `SIW_DATA_DIR`, journal routes return 503 `journal_storage_unconfigured`, rather than keeping records in memory that would be lost.
- **API:**
  - `POST /v1/trades/manual` and `GET /v1/trades/manual`.
  - `POST /v1/scenarios` and `GET /v1/scenarios`.
  - CORS now allows `POST`.
  - The server sets the assertion and creation time.
  - Users enter times in exchange-local ET. The server converts them to UTC and rejects the nonexistent spring-forward hour and the ambiguous fall-back hour.
  - Unknown instruments and unknown request fields (for example, an account number) are rejected.
- **P&L:**
  - The manual ledger computes FIFO realized P&L per instrument, net of fees, in `Decimal`, including shorts and flips through zero. Executions with an unknown time are listed but left out of the calculation, never placed at a guessed time.
  - Each scenario's P&L applies its slippage adversely at both entry and exit, and subtracts its total fees.
  - The two ledgers are returned by different routes and never summed.
- **UI:**
  - Clicking a candle opens a form for either an actual execution or a hypothetical scenario.
  - The candle's interval and OHLC are shown as context only. Price and time are entered by the user, and nothing is prefilled from the candle.
  - Markers use a distinct shape and colour per ledger: blue arrows for manual records, purple circles and squares for hypothetical entries and exits.
  - Two ledger panels are labelled "unverified" and "not real P&L".
- **Out of scope here:**
  - **Accounts:** there is no account field, so an account number cannot be stored. P&L is per instrument across one implicit account.
  - **Not yet built:** editing or voiding records (corrections), CSV/XLSX import, broker-confirmed executions and reconciliation.

## Alternatives

- **Prefill price and time from the candle:** faster entry, but it invites accepting the candle as the fill, which the domain rules forbid.
- **One table with a `ledger` column:** simpler queries, but a single missing filter would mix ledgers. Separate tables make mixing structurally hard.
- **Accept local times as UTC from the browser:** the browser would then own DST policy, and silently pick an hour on ambiguous days.

## Trade-offs

- Scenarios are fixed at version 1 until editing exists.
- The journal is local to one machine, like the cache; there is no sync or backup yet.
- With no account dimension, P&L across several real accounts would be combined. Accounts need a safe opaque-ID design before they are added.

## Evidence/verification

- `tests/journal/test_ledger.py`:
  - FIFO with partial closes and fees;
  - shorts and flips through zero;
  - unknown-time executions left out;
  - scenario slippage;
  - DST conversion, including the gap and the overlap.
- `tests/journal/test_journal_api.py`:
  - idempotent create, replay and conflict;
  - validation failures;
  - extra-field rejection;
  - separate ledgers with their own P&L;
  - 503 without a data directory;
  - CORS preflight for POST;
  - the separate database file.
- Contract fixtures for each new semantic rule, checked in both Python and TypeScript.
- `Workspace.test.tsx` and `journal-markers.test.ts` for the UI flows and markers.
- In the in-app browser, against a real API with a scratch `SIW_DATA_DIR`:
  - a manual buy recorded from a candle, showing on both charts;
  - a short scenario with net 2.00 = gross 3.00 − fees 1.00;
  - both records still there after a reload;
  - 12:47 ET stored as 16:47Z.

## Revisit when

- broker CSV import or broker-confirmed executions are added (reconciliation, `evidence_status` changes);
- users need to correct or void entries;
- multiple accounts are supported;
- backtest fills are introduced (a fourth, separate ledger).
