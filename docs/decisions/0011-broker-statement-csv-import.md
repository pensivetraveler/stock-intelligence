# ADR 0011 — Broker statement CSV import and duplicate review

- **Status:** accepted
- **Date:** 2026-10-17

## Context

Phase 3 asks for:

- CSV import with a mapping preview, validation and provenance;
- review of ambiguous duplicates;
- immutable import batches.

Its acceptance test is: "Import the same CSV twice without double-counting; flag ambiguous duplicates without deleting valid fills." The safety rules also forbid storing account numbers or secrets. Broker exports routinely contain an account column, and a file the user supplies does not prove that a broker confirmed the trades.

## Decision

- **A third ledger:** imported rows become `imported_execution` records with `verification_status: statement_imported`.
  - They are not `broker_confirmed_execution`, and they are kept apart from manual and hypothetical records, with their own FIFO P&L.
  - Manual and imported entries can describe the same real trade, so the two are never summed. Reconciling them is future work.
- **Preview, then commit:**
  - `POST /v1/imports/trades/preview` reads the CSV text and suggests a column mapping from the header. The user can change the mapping.
  - Each row is normalized or explained, and classified as `new`, `duplicate_exact`, `possible_duplicate` or `invalid`.
  - `POST /v1/imports/trades/commit` recomputes the preview on the server. It refuses until every possible duplicate has an explicit import or skip decision, then stores the batch and its rows in one transaction.
- **Duplicate rules:**
  - The same broker label and broker execution ID, with the same details, is an exact duplicate (already stored, or earlier in the same file) and is skipped. The same ID with different details is invalid, never merged.
  - Without a matching ID, a row is a possible duplicate when instrument, side, quantity and price match and the time matches at the coarser precision (minute or second). The user decides, so identical legitimate fills can both be kept; a kept row records `imported_after_review` and which records it resembles.
  - A file is identified by its SHA-256. A file already committed returns its original batch and imports nothing.
  - A unique index on (broker label, broker execution ID) backs this up at the database level.
- **Normalization:**
  - **Times:** an explicit offset wins. A timestamp without one is read in the chosen zone (ET or UTC); in ET, nonexistent and ambiguous DST times are rejected. Separate date and time columns are supported. Fractional seconds and future times are rejected.
  - **Values:** `$` signs and thousands separators are accepted. Parentheses and negative signs are read as negative. Fees are costs regardless of their sign. A negative quantity is allowed only on a sell. Common side words (bought, sold, sell short, buy to cover) are recognized.
  - **Symbols:** symbols must exist in the provider's instrument list.
- **Privacy:**
  - Only mapped columns are read. The file text is processed in memory and never stored. A batch keeps the file name, SHA-256 and row numbers as provenance.
  - The broker label must be a name; labels containing six or more digits in a row are rejected as likely account numbers. Unknown request fields are rejected.
  - Limits: 1 MB and 5,000 rows per file.
- **UI:**
  - A collapsible "Import broker statement (CSV)" panel: choose a file, enter the broker label, choose the time zone and delimiter, then Preview.
  - The preview has a mapping editor and a per-row status table. Each possible duplicate gets an import/skip choice, and Commit stays disabled until every one is decided.
  - Imported fills appear in their own ledger and as teal "I" markers on the chart.

## Alternatives

- **Treat imports as broker-confirmed:** wrong evidence level; a CSV can be edited.
- **Store the original file for audit:** it would keep account numbers and other unmapped data. The SHA-256 lets a user prove which file was imported without the journal holding it.
- **Merge possible duplicates automatically:** this would delete valid identical fills, which the acceptance test forbids.
- **Hash the account number into an opaque ID:** still derived from a secret identifier. Account support needs its own design.

## Trade-offs

- **No original file:** a batch cannot be re-parsed later with a different mapping. Fixing a mapping means voiding and re-importing, which is not built yet.
- **Single implicit account:** with no account dimension, statements from different accounts at one broker share one ledger.
- **Broker-specific formats:** there are no presets per broker yet; the header heuristics and manual mapping cover common exports.

## Evidence/verification

- `tests/journal/test_csv_import.py`:
  - header suggestions;
  - normalization of signs, currency and ET times;
  - exact and conflicting broker IDs, in the same file and against stored records;
  - possible duplicates within a file and against stored records without IDs;
  - timestamp formats, plus DST, fractional-second and future-time rejection;
  - unreadable files, mapping rules, and the broker-label guard.
- `tests/journal/test_import_api.py`:
  - preview stores nothing;
  - the same file imports once;
  - the account number never reaches the response or the database file;
  - an overlapping statement imports only new fills;
  - a decision is required, and both copies can be kept;
  - skip decisions are recorded;
  - the import ledger is separate from the manual one;
  - bad requests are rejected, and imports return 503 without a data directory.
- Contract fixtures for each new semantic rule, checked in both Python and TypeScript.
- `Workspace.test.tsx`: the import flow with a duplicate decision and the third ledger.
- In the in-app browser, against a real API:
  - a 4-row statement previewed as 2 new, 1 already imported and 1 invalid;
  - 2 rows imported, with realized −0.30 = +1.20 − 1.50 fees;
  - re-previewing the same file showed "already imported";
  - `journal.sqlite3` does not contain the account number.

## Revisit when

- broker-specific presets or XLSX files are needed;
- users need to void a batch or re-import with a corrected mapping;
- manual entries should be reconciled with imported fills;
- multiple accounts must be separated (an opaque account design).
