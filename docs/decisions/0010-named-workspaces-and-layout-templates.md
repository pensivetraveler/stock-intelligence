# ADR 0010 — Named workspaces and layout templates

- **Status:** accepted
- **Date:** 2026-10-16
- **Amends:** ADR 0008 (single saved layout)

## Context

The spec asks for Trading Terminal and Trade Review layouts, reusable presets applied only on explicit request (creating independent copies, not live links), and undo. ADR 0008 deferred any domain-store persistence until named workspaces or presets existed.

## Decision

- **Named workspaces:** a device-local workspace library (`siw.workspaces.v2` in `localStorage`) holds named workspaces.
  - The active workspace saves as it changes.
  - You can save as a new name, switch, rename (with unique, trimmed names of at most 60 characters) and delete, except the last workspace.
  - A v1 single layout migrates as "Default".
- **Layout templates:**
  - Two templates: Trading Terminal (1m and 15m, extended hours, independent) and Trade Review (5m and 60m, regular hours, synced).
  - Applying one is an explicit button. It copies timeframe and hours into each chart, keeps each chart's instrument and date, and shares no objects with the template.
  - Template, reset and undo actions keep an undo stack of 20 per workspace.
- **Where workspaces live:** workspaces stay out of the backend for now. They hold no journal or analysis data. They move to the API when replay or Agent analysis must reference a workspace context (Phase 5), which is when the backend needs them.

## Alternatives

- **Store workspaces through `GET`/`PUT /v1/workspaces/{id}` now:** this adds write routes, migration and conflict handling with no consumer on the backend yet.
- **Templates as live links:** this contradicts the spec's rule that applying a preset creates independent instances.

## Trade-offs

- Workspaces do not follow the user to another device or browser profile, and are lost if site storage is cleared.
- Indicator presets (Phase 4) will need the same explicit-apply and undo model per chart. This ADR covers layout only.

## Evidence/verification

- `workspace-library.test.ts`:
  - migration from v1;
  - the storage round trip;
  - copy independence;
  - name rules;
  - keeping the last workspace;
  - template application without shared objects.
- `Workspace.test.tsx`: applying a template, undo, and save-as/switch/rename/delete.
- The Trade Review template was applied in the in-app browser.

## Revisit when

- Phase 5 replay or Agent analysis needs a server-side workspace reference;
- cross-device sync or more than two charts are required.
