# Stock Intelligence Workstation — Codex Implementation Roadmap v1.5

Status (2026-10-16): Phase 0와 실행 단계 Phase 1A~1C, 합성 데이터 기반 Phase 1 내부 마무리(1E: 공개 집계 API·cache 범위 조합·재개 가능한 backfill), Phase 2A·2B 차트 workspace(차트 2개·동기화·이름 붙인 workspace·템플릿), Phase 3A 수동·가상 거래 원장, Phase 3B 거래내역 CSV 가져오기의 로컬 구현 완료. 실제 Provider 수집·정정 처리(1D), Phase 3의 정정·대사·broker 확인, Phase 4 이후는 남아 있다. Tauri native와 원격 CI는 미검증이다. 현재 증거는 [PROJECT_STATE.md](PROJECT_STATE.md), 커밋별 작업은 [IMPLEMENTATION_HISTORY.md](IMPLEMENTATION_HISTORY.md), 실행 순서·승인 범위는 [EXECUTION_PROCESS.md](EXECUTION_PROCESS.md)를 따른다.

아래는 전체 제품의 목표 목록이다. 항목의 존재를 구현 완료로 해석하지 않는다. 특히 하단 Vendor verification의 Phase 1A/1B는 조사 작업 흐름의 기존 명칭이며, 실행 단계의 Fake Provider/로컬 저장 Phase 1A/1B와 다르다. 실제 Provider 시험은 실행 단계 Phase 1D에 해당한다.

## Engineering approach

Build vertical slices with demonstrable behavior, tests, and rollback. Use fixtures and fake adapters before committing to vendor integrations. One phase is complete only when its acceptance checks pass.

### Phase 0 — Repository and contracts

- Establish monorepo structure: apps/desktop, services/api, services/collector, packages/contracts, packages/indicator-sdk, tests/fixtures, docs/decisions.
- Define domain schemas for bars, instruments, executions, scenarios, indicator definitions and agent evidence.
- Configure formatting, lint, type checks, unit/contract tests and CI. 데이터베이스가 도입되기 전에는 migration 도구나 빈 migration을 만들지 않으며, 실제 SQLite 메타데이터 저장이 시작되는 Phase 1에서 migration 전략과 복구 테스트를 추가한다.
- Deliver: skeleton starts locally, sample API health and version endpoints work, test suite passes.

### Phase 1 — Market data foundation

- Implement provider interface with fake fixture provider first, real vendor later.
- Normalize UTC timestamps, sessions, adjusted/raw policy, stable instrument IDs.
- Store partitioned Parquet bars, collection job status and coverage manifests in SQLite; gap detection, idempotent backfill, vendor throttling, revision pinning and crash recovery.
- Implement session-aware 1m→5m/15m/30m/60m aggregation, explicit partial bars, streaming/polling capability abstraction and provider correction handling.
- Deliver: request a date range, cache it, restart and request again without duplicate bars; detect missing intervals.

### Phase 2 — Chart and workspace

- Tauri/React shell, chart rendering, symbol/timeframe selection, terminal/review presets, chart synchronization groups, workspace persistence.
- Deliver: two charts, independent symbols/timeframes, sync toggle, layout saved/reloaded.

### Phase 3 — Trade journal and scenario interaction

- Chart candle click -> actual/manual and hypothetical dialogs; chart markers and distinct ledger views.
- CSV/XLSX mapping preview, validation, provenance, ambiguous duplicate review, immutable import batches.
- Deliver: import fixtures, add manual trade, build scenario, reconcile potential duplicate, independent P&L.

### Phase 4 — Indicator platform

- Built-ins (SMA/EMA/VWAP/RSI/volume initially), definition registry and per-chart instances.
- Preset save/apply with append/replace preview, bulk apply, undo and no implicit propagation.
- Python indicator SDK with isolated worker, resource limits, output validation, warmup handling.
- Minimal visual composition using a safe expression/graph DSL; no arbitrary Python generation from UI expressions.
- Deliver: Python and UI versions of same formula agree on fixtures; replay cutoff prevents lookahead.

### Phase 5 — Replay and agent integration

- Point-in-time replay service, partial-bar policy, evidence availability gating, versioned analysis context.
- Integrate one end-to-end read-only agent workflow, e.g. Chart/Trade Review Agent, before more agents.
- Deliver: repeatable analysis with cited input bars/indicator settings, agent suggestion cannot write confirmed fills.

### Phase 5B — Backtesting MVP (after replay/indicator contract stabilization)

- Define versioned Python and visual-rule strategies with isolated execution, indicator dependency pinning and immutable run configuration.
- Implement deterministic finalized-bar-close signals, next-bar-open market fills, configurable fees/slippage, long-only USD portfolio ledger, coverage validation and result persistence.
- Show backtest metrics, equity curve, trade list and distinct chart overlays; prevent backtest fills from entering real/manual ledgers.
- Deliver: fixture strategy produces exact expected fills/P&L, identical reruns, no lookahead, and explicit warnings for gaps/unsupported fill assumptions.

### Phase 6 — Broker adapters and advanced agents

- Validate broker capabilities individually: Korea Investment, Kiwoom, Toss; Mirae Asset only if chosen and verified.
- Add broker adapters with read-only permissions, secret management, incremental sync and reconciliation.
- Integrate News, News Cycle, Dark Pool as separate agents through common evidence schema.
- Deliver: broker fixture contract tests, partial failure recovery, provenance for every imported fill.

### Phase 7 — Remote collector and tick expansion

- Move collector behind authenticated API/WebSocket, secure remote deployment, health checks, backup and restore.
- Introduce tick store/schema and separate tick-specific analytics; do not infer tick executions from 1-minute bars.
- Deliver: switch collector endpoint without UI rewrite; recover from disconnects and replay retained evidence.

## Backtesting follow-on (post-MVP)

- Limit/stop and partial-fill models, liquidity and volume participation, corporate actions and FX, survivorship-aware historical universes, walk-forward/out-of-sample validation, multi-symbol portfolio controls and eventual tick-based execution modeling.

## Cross-cutting test matrix

- Timezone and DST; premarket and after-hours; holidays and half days.
- Corporate actions and source corrections; raw vs adjusted prices.
- Partial fills, duplicate imports, identical legitimate executions, conflicting broker/file/manual records.
- Incomplete bars, indicator repaint, future-data leakage, missing warmup history.
- Resource-exhausting or failing Python indicators and strategies; deterministic backtest reruns and ambiguous OHLC intrabar order.
- Data migrations, interrupted jobs, disk-full behavior, backup and restore.

## Post-Phase-0 vertical slice (Phase 1~3 통합 목표)

아래 항목은 **Phase 0 수락 조건이 아니며**, Phase 0 완료 후 Phase 1~3에서 순서대로 구현할 첫 사용자 기능 통합 목표다.
**Prerequisite:** Phase 0 repository scaffold, schemas and quality gates have passed.

1. Serve fixture 1-minute bars from fake provider through API.
2. Render one chart in desktop app.
3. Click a candle and create a manual actual execution or hypothetical scenario.
4. Persist each separately and render distinct markers after restart.
5. Demonstrate tests for no cross-contamination of actual and simulated P&L.

Do not begin with live brokerage credentials or autonomous trading.

## Multi-source integration workstream

> v1.2에서 도입되어 현재 v1.5 로드맵에 통합된 작업 흐름이다.

- Phase 0: define source registry, capability/entitlement contract, source provenance, provider-class placeholders and synthetic contract fixtures. 실제 provider adapter와 fixture-serving market-data adapter는 Phase 1에서 시작한다.
- Phase 1: implement canonical-source routing, cache-first coverage, explicit fallback and source-conflict records; verify provider docs, terms and sample responses before live adapters.
- Phase 1B: add acquisition budgets, retries, per-provider health and data quality dashboard; never silently splice heterogeneous OHLCV.
- Phase 5B: backtests pin the selected price-source segments/revisions and label retrospectively acquired web evidence; reject unknown critical data coverage.
- Phase 6: add broker account API adapters separately from broker market-data adapters; official feeds first, permitted crawling second; enforce retention/attribution/licensing constraints.
- Acceptance: fixture tests for conflicting bars, stale and delayed data, adjusted/raw mismatch, provider outage/fallback, source-switch warnings, duplicate crawled events, point-in-time availability and credential revocation.

## Vendor verification before costly data subscriptions

> v1.3에서 도입되어 현재 v1.5 로드맵에 통합된 작업 흐름이다.

**Research artifact:** `DATA_PROVIDER_EVALUATION_v1.5.md`.

### Phase 0A — Capabilities & contract fixtures

- Register Toss, KIS, Kiwoom, Massive, Alpaca, Databento, SEC EDGAR and FINRA OTC as capability placeholders with fields for documented support vs actual account entitlement. 등록은 외부 호출 가능한 adapter 구현을 의미하지 않는다.
- Build synthetic, provider-shaped contract fixtures for pagination, feed labels and entitlement states. 공식 예시에서 유래한 필드는 출처와 문서 확인일을 기록하고 실제 응답 fixture로 오인하지 않도록 `synthetic`으로 표시한다.
- Test source identity, source feed scope (IEX/SIP/venue), timestamp normalization, source correction, license/retention fields and no auto-splicing.

### Phase 1A — Broker 1-minute read-only POC

- With user-approved API credentials, test 5d/30d/1y/5y sample ranges as entitled; note HTTP quota, historical cutoff, sessions, adjusted/raw policy and rate limits. Never place keys in committed files or logs.
- Verify incomplete vs no-trade minutes, DST, duplicate pages and unknown historical availability.
- Emit provider capability report + gap/coverage manifest + error matrix. Record **cannot test without credentials** rather than claiming verified integration.

### Phase 1B — Commercial feed evaluation gate (vendor-neutral)

- Prototype **both Alpaca and Massive** with equivalent fixture contracts; where the user has approved credentials/plans, run comparable historical 1m, pagination, coverage, latency, extended-hours, revision, and licensing tests. If one cannot be tested, mark `not_tested` and preserve both as candidates.
- Independently test real-time feed scope (IEX vs consolidated/SIP vs venue) where entitled; do not assign historical exclusively to Massive or streaming exclusively to Alpaca.
- Compare vendor/plan combinations for charting, local retention and backtests against broker output. Report a capability/quality/cost matrix; do not choose, subscribe to, or hard-code a paid provider until requirements and verified evidence justify it.
- Reassess Databento only when a distinct Tick/L2 use case warrants it, with explicit spending approval.

### Phase 6A — Official auxiliary feeds

- Build SEC EDGAR JSON adapter respecting published User-Agent/fair access and FINRA weekly ATS/non-ATS adapter; mark delayed weekly summaries as distinct from intraday dark-pool estimates.
- IR/news HTML crawl requires per-domain terms/robots/copyright review, minimal metadata/excerpts and publication-vs-observed timestamps.

### Exit gate

- One validated historical US minute feed with documented access/storage rights, exactly reproducible pinned 30-day test dataset, budget recorded, and source-aware backtest/agent replay; at least one tested broker account-import mapping. All missing support/entitlement is stated openly.
