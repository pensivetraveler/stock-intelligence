# ADR 0025 — Broker reconciliation (read-only, computed)

- **Status:** accepted
- **Date:** 2026-10-21

## Context

The journal keeps four ledgers apart (ADR 0009, 0011, 0018):

- manual executions: user assertions, time precision exact, minute, approximate or unknown;
- statement imports: rows from a broker CSV, time exact or minute;
- broker API executions: one record per order from Toss (total quantity, average price, the last fill's time);
- hypothetical scenarios.

The first three can describe the same real trades. Roadmap Phase 6 asks for "incremental sync and reconciliation", and SYSTEM_SPEC §3 says a manual execution "may later reconcile with broker evidence". Until now the user could only compare the ledgers by eye.

## Decision

### A computed view, never a write

- **Endpoint:** `GET /v1/trades/reconciliation?instrument_id=&since=&price_tolerance_bps=` returns `trade_reconciliation_response`, with `view: "computed"`.
- **Reads only:** it reads the three actual-fill ledgers. It does not merge, rewrite or annotate them, and stores nothing. A test checks that the journal file's bytes are unchanged.
- **Evidence status unchanged:** a manual record's `evidence_status` stays as recorded. Promoting it to `reconciled` would be a new, user-approved append, which is deferred.
- **Scenarios:** hypothetical scenarios are never compared.
- **Decimals:** amounts are compared as `Decimal`. Differences are returned as decimal strings.

### Time windows

Each record becomes a window of when its fill(s) can have happened:

| Record               | Window                                                                  |
| -------------------- | ----------------------------------------------------------------------- |
| broker order         | [ordered at, reported last fill]; open-ended when no fill time is given |
| exact                | that instant                                                            |
| minute               | [t, t + 59 s]                                                           |
| approximate (manual) | t ± 3 h                                                                 |
| unknown (manual)     | anything before the moment the user recorded it                         |

Two records are time-compatible when their windows overlap, give or take 2 minutes (clock and rounding slack).

### Matching, per instrument and side

The manual and the imported ledger are each compared with the broker ledger on their own. One broker order can therefore be matched by a manual entry and by a statement row at the same time. The passes run in this order:

1. **Order ID link:** a statement row whose `broker_execution_id` equals the broker's order ID belongs to that order, whatever its time.
   - It is matched when the linked rows' quantity sums to the order's and their average price is within the tolerance.
   - Otherwise it is conflicting.
2. **One-to-one:** equal quantity, price within the tolerance, compatible time. The closest in time wins, with ties broken by price distance, then by how few alternatives either side has, then by IDs.
   - Then pairs are shifted along augmenting paths (Kuhn's algorithm) wherever that pairs one more record, so the number of one-to-one matches is the maximum possible. Without this step, a record fitting two orders could take the only order another record fits; on 5,710 real orders mirrored as manual entries, greedy alone left 20 pairs unmatched.
   - A record with an approximate or unknown time that fits several orders is marked `ambiguous_candidates` and left unmatched. It is never assigned by guess.
3. **Aggregated fills:** two or more exact- or minute-precision records inside one order's window.
   - Their quantities must sum to the order's, and their quantity-weighted average price must be within the tolerance.
   - The broker reports no individual fills, so this is the only way to reconcile partial fills.
4. **Conflicts:** an order and a record that are still unpaired, have the same side and a compatible time, but differ in quantity or price.
   - Closest first, one record per order.
   - Records with an unknown time are never paired here: nothing ties them to a particular order.

**Price tolerance:** relative, in basis points of the broker's average price. The default is 10 bps and the query can set 0–9999.99. Quantity must be equal.

### Classification

Every record gets exactly one status:

- **Broker order:** `conflicting` if any counterpart conflicts, otherwise `matched` if any counterpart matches, otherwise `broker_only`.
- **Manual or imported record:** `matched`, `conflicting`, or `manual_only` / `imported_only`.

Each record also lists:

- `counterparts`, each with the ledger, execution ID, relation, quantity difference and price difference in bps. Both differences compare the counterpart group with the broker order.
- Reason codes: `order_id_link`, `aggregated_fills`, `time_unknown`, `time_approximate`, `broker_time_unknown`, `quantity_differs`, `price_differs`, `ambiguous_candidates`, `no_broker_counterpart`, `no_manual_counterpart`, `no_imported_counterpart`.

On a broker order the reasons are the union over both ledgers; `counterparts` says which ledger each one came from.

### `since`

`since` filters the records returned, not the input. A record just before the boundary still counts as a counterpart, so a match is never lost there.

### Screen

The trade journal panel shows a "Reconciliation" section under the ledgers, in English and Korean:

- counts per status;
- by default only the records that need attention, with an "All records" option;
- each record's ledger, time and basis, status, counterparts with differences, and reasons;
- a "Recompute" button.

It reloads whenever the journal ledgers reload, for example after a Toss sync or an import.

## Alternatives

- **Store match results or link records in a new table:** rejected for now. It would add state that can go stale when a ledger gains records, and the computation is cheap at personal-journal sizes.
- **Mark records `reconciled` automatically:** rejected. A user assertion should change only through the user.
- **Minimum-cost assignment (Hungarian) and subset-sum search for partial fills:** rejected. Closest-first pairing plus maximum-cardinality augmentation is deterministic and explainable. Aggregation is limited to "everything inside one order's window" instead of searching subsets.
- **Absolute price tolerance:** rejected because USD and KRW prices differ by orders of magnitude.

## Trade-offs

- **Not minimum-cost:** the match count is maximal, but when several equal orders overlap in time, the pairing among them is not guaranteed to have the smallest total time distance. Loose times guard against this by refusing ambiguous assignments.
- **All-or-nothing aggregation:** aggregation uses every eligible record in the window. One unrelated fill of the same side inside a long-running order's window prevents the aggregate match, and the order then shows as conflicting.
- **Fixed windows:** the approximate window (±3 h) and the time slack (2 min) are constants. They are reported in the response's `parameters`.
- **Toss only:** broker records come from Toss only. The order ID link works only if the statement's execution ID column holds Toss order IDs.

## Evidence/verification

- `tests/journal/test_reconcile.py` (13 tests) covers:
  - one-to-one and per-ledger independence;
  - the bps tolerance;
  - quantity conflicts;
  - windows for minute, approximate and unknown times;
  - ambiguity;
  - aggregated fills and order ID links;
  - `since`;
  - determinism and inputs left unmodified;
  - a record with one fitting order not crowded out by one with two.
- `tests/journal/test_reconciliation_api.py` covers the contract, filters, rejected parameters, the 503 without storage, and the unchanged journal bytes.
- Desktop tests: `ReconciliationView.test.tsx`, plus the journal panel in `Workspace.test.tsx`.
- A live run against a scratch journal is recorded in PROJECT_STATE.

## Revisit when

- The user wants to confirm matches; this would need an append-only confirmation ledger.
- A second broker API ledger is added.
- Journals grow large enough that computing on every request is slow.
- Brokers start reporting individual fills.
