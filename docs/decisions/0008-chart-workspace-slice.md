# ADR 0008 — Phase 2A chart workspace slice

- **Status:** accepted
- **Date:** 2026-10-16

## Context

The roadmap's Phase 2 deliverable is two charts with independent instruments and timeframes, a sync toggle, and a layout that is saved and restored. The workstation's rules also require:

- each chart's settings stay independent;
- source, session, quality and Coverage are shown, never hidden;
- exchange time is shown with DST handled correctly;
- only synthetic data is used until a provider is approved.

The only synthetic instrument had four bars, which is too thin for a chart.

## Decision

- **Chart library:** `lightweight-charts` 5.2.1 (Apache-2.0, published 2026-08-12). It provides canvas candlesticks, pan/zoom and time-scale events without a framework dependency. The default TradingView attribution logo is kept, as its license notice requires.
- **Data:** a deterministic generator (`services/market_data/synthetic.py`) adds an invented `SYNTH` instrument to the fake-provider fixture.
  - Coverage is 2026-10-09, 08:30–17:00 ET, from a seeded random walk.
  - It has a deliberate 3-minute gap and one provisional final minute.
  - A test fails if the committed fixture drifts from the generator.
- **API additions (read-only):**
  - `GET /v1/market/instruments` returns the `market_instruments_response` contract: instrument ref, revision and exchange session dates.
  - `GET /v1/market/sessions/{date}` returns the `session_window` contract: UTC bounds from the backend calendar, with null bounds on closed days.
  - The UI never computes exchange hours or DST itself.
- **Contracts and validation:**
  - The two new contracts get semantic checks in both Python and TypeScript: ordered session bounds, no bounds on a closed day, and unique instruments.
  - Python validates each document against only its own kind's definition. This is equivalent to the root `oneOf`, because each branch pins `kind`, and about ten times faster.
- **Workspace:**
  - Two panels, each with its own instrument, timeframe, session date and regular/extended hours.
  - A visible-time-range sync group that never echoes a range back.
  - Partial or provisional bars are drawn grey.
  - Each panel lists Coverage, bar quality, sessions, lineage (source, feed, adjustment, revision, verification) and missing intervals in ET.
  - The layout is saved to `localStorage` under a versioned key. Anything malformed falls back to the default layout.
- **UI response checks:** the desktop imports the generated contract types only. The API validates every response before sending it. The client checks only the response kind and the error envelope, because Tauri's CSP forbids the `eval` that a runtime schema compiler (ajv) needs.

## Alternatives

- **Hand-written SVG chart:** no dependency, but it would need pan, zoom and crosshair re-implemented.
- **Persist the layout in the local SQLite store:** this would survive storage clears and be shared with the API. A UI preference does not need the domain store yet; revisit when there are named workspaces or presets.
- **Compute session bounds in the browser with `Intl`:** this duplicates the calendar and DST policy and could drift from the backend.

## Trade-offs

- Prices become JavaScript numbers for drawing only. Domain values stay as contract decimal strings, and no calculation is done on the display numbers.
- Extended-hours Coverage counts the policy's 04:00–20:00 ET buckets. The synthetic day is honestly reported as partial outside 08:30–17:00 ET.
- The fixture grew to about 0.5 MB, and provider start-up takes about 0.8 s including the calendar load.

## Evidence/verification

- `tests/api/test_api.py`: instruments, session windows, extended-hours aggregation, and CORS headers on unexpected errors.
- `tests/market_data/test_synthetic_fixture.py`, plus the new contract fixtures (valid and invalid) used by both validators.
- Desktop tests:
  - `workspace-state`, `chart-data`, `chart-sync` and `market-api` unit tests;
  - `Workspace.test.tsx` with the canvas chart mocked.
- In the in-app browser, against a real uvicorn server and Vite:
  - two charts rendered with ET axes;
  - zoom stayed in sync;
  - instrument and date changes worked, including the holiday message;
  - the layout was restored after a reload;
  - a 375 px mobile width had no horizontal scroll.
- Browser verification found two defects, both fixed with regression tests:
  - extended-hours buckets were ordered by session name;
  - unexpected 500 responses lacked CORS headers.

## Revisit when

- named workspaces, review/terminal presets or more than two charts are added;
- indicators or trade markers are drawn (Phase 3–4);
- the Tauri native build is verified, including whether the CSP allows the chart and the attribution link.
