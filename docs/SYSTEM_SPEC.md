# Stock Intelligence Workstation — System Specification v1.5

- **Status:** Draft for implementation. Last updated: 2026-10-12.
- **Version policy:** 문서 전체의 현재 유효 버전은 v1.5다. 아래 절 번호는 요구사항의 주제 구분이며 과거 버전 우선순위를 뜻하지 않는다. 변경 이력에서 명시적으로 `superseded`된 규칙만 무효이고, 충돌 시 현재 사용자 지시 → 검증된 코드/테스트 → 이 문서의 뒤쪽에 있는 더 구체적인 확정 규칙 → 일반 규칙 순으로 적용한다.

## 1. Mission and boundaries

Build a local-first US equities desktop workstation for chart-centered trade review, hypothetical trade scenarios, indicator development, and evidence-grounded collaboration with specialized AI agents. No autonomous order execution is in scope. The user retains trading decisions.

## 2. Confirmed requirements

- US equities only; primary data granularity 1-minute OHLCV; tick support later.
- External market-data APIs plus selective local cache. Collector and desktop/chart services logically separate from day one; co-located initially, remote collector later.
- Both Trading Terminal and Trade Review workspace layouts; customizable panels, synchronized or independent charts; per-chart independent indicators by default.
- Built-in indicators and custom indicators authored in Python or a visual composition UI; reusable presets applied to one or multiple charts on explicit request. Applying a preset creates independent chart instances, not live links.
- Actual executions imported via broker APIs, CSV, Excel, or entered manually by clicking a chart candle; hypothetical entry/exit scenarios also created via chart interaction.
- Actual, manually asserted, and hypothetical records are separately stored, visualized, and evaluated. A candle selection suggests a time interval, never an exact execution timestamp or price.
- Broker scope: Toss Securities, Korea Investment & Securities, Kiwoom Securities; Mirae Asset under consideration. Broker-specific API feasibility unverified; Webull–Mirae connectivity unverified.
- Backtesting is required: Python and visual-rule strategies, deterministic historical runs, simulated execution and portfolio accounting, metrics, chart overlays, and agent review. Backtest fills and performance must remain separate from real and manually entered trades.
- Specialized agents envisioned: Chart, Trade Review, News, News Cycle (including listed-company analogs), Dark Pool. Their data contracts must preserve source and time provenance.

## 3. Suggested architecture (not yet locked technology)

Desktop: Tauri 2 + React/TypeScript, Lightweight Charts and a dockable panel library.
Local backend: Python FastAPI exposing typed HTTP endpoints and optional WebSocket events.
Collector: separate service and scheduler, initially same PC; later authenticated network API to remote server.
Storage: SQLite for application metadata, journal, and state; partitioned Parquet for bars; DuckDB for analytics; Polars/PyArrow for transforms.
Contracts: versioned JSON Schema/OpenAPI; adapters for market data and broker data; indicator output contract; agent evidence contract.
Do not share SQLite files over a network. A remote collector must expose an API with authentication, retries, and idempotency.

## 4. Domain model

- Instrument: stable instrument_id, symbol history, venue, currency, lifecycle.
- MarketBar: instrument_id, timeframe, interval_start_utc, OHLCV, source, adjustment policy, market session, quality status, ingested_at, dataset revision.
- CollectionJob: provider, symbol/range, status, retries, idempotency key, error.
- BrokerAccount: internal opaque account ID, broker, account alias; secrets stored outside DB or encrypted by OS vault.
- SourceArtifact: imported file checksum or API request batch; source metadata and immutable original reference.
- Execution: stable internal ID, broker-native execution ID when available, account, instrument, side, quantity, price, executed_at_utc, fees/currency, provenance, verification state, correction lineage.
- ManualExecution: user-entered assertion linked to a selected bar interval, with independently entered exact/approximate time and price; may later reconcile with broker evidence.
- TradeGroup: position/round-trip grouping policy and linked executions; not a substitute for immutable fills.
- SimulatedScenario: versioned scenario ID, hypothetical legs, assumptions for spread/slippage/fees/fill model; isolated from real account/P&L.
- ChartAnnotation: visual anchor (instrument, timestamp/interval, price), annotation type, linked record ID.
- IndicatorDefinition: ID/version, authoring type (built-in/Python/visual), inputs, parameters, warmup, outputs, repaint policy, code/graph hash.
- IndicatorInstance: chart ID, definition version, parameters, style, pane, visibility; independent after preset application.
- Workspace: layout, charts, synchronization groups, preset references; separate from journal data.
- BacktestStrategy: versioned strategy definition, Python or visual DSL, parameters, required datasets and indicator dependencies, code/graph hash.
- BacktestRun: immutable configuration snapshot, instrument universe, date range, market-data revision, point-in-time policy, capital/risk settings, cost and fill assumptions, engine version, seed, status.
- BacktestOrder/Fill/Position/EquityPoint: simulated order intent, deterministic fill decision and reason, positions, cash, equity and fees; never broker executions.
- AnalysisRun/Evidence: agent identity/version, request context, point-in-time cutoff, source references, indicator versions, generated findings and uncertainty.

## 5. Key invariants

1. No actual/simulated P&L mixing. A scenario never changes a broker account ledger.
2. Keep source artifacts and corrections; do not silently overwrite confirmed fills.
3. Import idempotency: broker+account+native execution ID where reliable; fallback fuzzy match only flags possible duplicates for review. Legitimate identical fills may coexist.
4. UTC for internal timestamps; America/New_York display and exchange calendar with DST, pre/regular/postmarket.
5. Explicit raw vs adjusted prices, splits/dividends, data source and dataset revision. Execution prices are never retroactively adjusted as if broker fills changed.
6. Replay and indicators may use only data available by cutoff; distinguish event time, publication time, ingestion time, and availability time where relevant. Partial bars must not masquerade as closed bars.
7. Python indicator code runs outside the main API process with resource limits and an explicit filesystem/network policy; user approval for agent-generated code before execution.
8. Backtest results are simulated, reproducible and isolated from real trade ledgers; strategies cannot access future bars or final values of unfinished bars. Report fill assumptions, data coverage, costs, survivorship limitations and uncertainty.
9. Trading advice from agents is analytical, not execution authority; distinguish facts, inference, hypotheses, and simulated outcomes.

## 6. UI interaction contracts

- Candle click -> context menu: actual execution, hypothetical scenario, annotation, ask agent.
- Actual execution form: account, side, quantity, price, execution time and precision, fees, memo, evidence status. Candle interval prefilled as context only.
- Scenario form: entry/exit legs, quantity or notional, fill assumptions, scenario version.
- Chart layers: distinct shapes plus colors for broker-verified, manual, hypothetical, agent suggestions. Same-bar markers aggregate with drill-down.
- Indicator controls: per-chart add/configure/show/hide, save preset, apply preset to selected charts (append or replace preview), undo.
- Replay: deterministic cutoff, synchronized chart groups, explicit incomplete bars, evidence availability masks.

## 7. API boundaries (initial draft)

GET /v1/instruments/search
GET /v1/market/bars?instrument_id=&resolution=&from=&to=&adjustment=
POST /v1/collection/jobs
GET /v1/collection/jobs/{id}
POST /v1/imports/trades/preview
POST /v1/imports/trades/commit
GET /v1/trades/executions
POST /v1/trades/manual
POST /v1/scenarios
POST /v1/indicators/validate
POST /v1/indicators/calculate
GET /v1/workspaces/{id}
PUT /v1/workspaces/{id}
POST /v1/backtests/runs
GET /v1/backtests/runs/{id}
GET /v1/backtests/runs/{id}/results
POST /v1/analysis/runs
All writes use validation, provenance, appropriate authorization, and idempotency where needed. Exact routes may evolve after OpenAPI contract review.

## 8. Open decisions / research

- Market data vendor: historical 1-minute coverage, pre/postmarket, corporate actions, cost, redistribution/license and rate limits.
- Broker-by-broker US stock execution API availability, access constraints, historical windows, CSV schemas and fees/currency normalization.
- Relationship between Webull API and Mirae Asset: do not assume compatibility.
- Docking library choice and Tauri packaging constraints.
- Cross-device backup/encryption strategy; scale and retention limits.
- Backtest universe and historical delisting coverage, vendor entitlements, survivorship-bias controls, benchmark and FX policy.
- Simulation fill policy and treatment of short sales, partial fills, FX conversion, fees and taxes.

## 9. Acceptance gates

- Given a sample US equity 1-minute series, display candles with correct timezone/session and no duplicate intervals.
- Import the same CSV twice without double-counting; flag ambiguous duplicates without deleting valid fills.
- Add an actual manual execution and a simulated entry/exit from chart clicks; verify separate persistence, markers and P&L.
- Two charts with different indicators remain independent after one chart's parameters change; bulk preset application is explicit and undoable.
- Replay at 10:35 ET never exposes finalized future candles, indicators, or post-cutoff news.
- Restart collector during an interrupted backfill; recover without missing or duplicating bars.
- A failed custom Python indicator cannot crash the desktop/backend or access disallowed resources.
- Backtest the same strategy twice against identical immutable dataset revisions and settings; assert identical fills/equity and no future-bar access.
- Reopen a workspace and reproduce its layout, chart settings and referenced analysis context.

## 10. Market Data Acquisition & Timeframe Specification (implementation contract)

### 10.1 Provider interface and selection gate

- Define `MarketDataProvider` methods for capabilities, historical bars, incremental/latest bars, sessions/calendar, corporate actions, and revisions if offered. Record provider ID, entitlement, rate limits, latency, historical depth, pre/postmarket availability, raw/adjusted semantics, symbol mapping, and licensing. These are capabilities to verify, not assumed vendor facts.
- Implement fixture provider before live vendor. Do not silently mix providers for a requested continuous series: provenance and merge precedence must be explicit.
- 1-minute OHLCV is canonical intraday storage. Request larger timeframes from local aggregation when coverage is adequate. Daily/weekly/monthly may use direct provider bars if minute coverage or official daily semantics differ.

### 10.2 Canonical bar contract

`instrument_id`, `interval_start_utc` (inclusive), `interval_end_utc` (exclusive), `resolution`, `open`, `high`, `low`, `close`, `volume`, `session`, `is_final`, `quality`, `provider_id`, `provider_symbol`, `adjustment`, `dataset_revision`, `available_at_utc`, `ingested_at_utc`. Decimal precision and volume units must be provider-aware; reject invalid OHLC relationships, negative volume, duplicates or timestamp misalignment. Preserve original payload/checksum reference for audit.

### 10.3 Query, cache, backfill

1. Resolve stable instrument ID and exchange calendar. Compute expected trading intervals within requested [from,to) range and requested session coverage; distinguish closed sessions, no-trade minutes, provider omissions and unknown coverage. Do not fill a no-trade minute with invented OHLCV.
2. Read cached bars plus a separate coverage manifest (range, provider, adjustment, revision, verified state, last checked). Cache presence alone does not prove completeness.
3. Return cached verified data quickly with `coverage_status`; enqueue missing/unverified ranges as idempotent collection jobs. API may expose pending/partial status rather than blocking indefinitely.
4. Chunk requests to vendor limits, throttle using actual quotas, apply bounded exponential retry for transient failures, checkpoint jobs, and recover on restart. Permanent entitlement/unsupported-range failures must be visible.
5. Validate, deduplicate on canonical identity + revision, write atomically to Parquet partitions and update coverage manifest transactionally or via recoverable commit journal. Corrections create new dataset revisions; pin revisions referenced by analysis/backtests.
6. Prefer selective long-term retention for traded/watchlisted/analyzed symbols; temporary cache uses explicit eviction policy. Never evict pinned evidence.

### 10.4 Timeframe aggregation

- Aggregate 1m to 5m/15m/30m/60m using first open, max high, min low, last close, sum volume, in chronological order. Define alignment relative to exchange session start or documented wall-clock boundary; never assume UTC modulo alignment is correct across DST.
- Respect premarket, regular and after-hours boundaries. Do not combine different sessions into one candle unless an explicitly selected chart policy says so.
- Each aggregate includes expected/observed source minute counts, `is_final`, and quality/coverage flags. Missing or provisional source minutes propagate uncertainty; do not silently present incomplete aggregates as complete.
- Intraday derived bars and direct-provider daily bars have distinct lineage. Do not assume identical values when raw/adjusted policies or session definitions differ.

### 10.5 Live and replay

- Support stream or polling adapter according to verified vendor capability; distinguish updates to an open candle from finalization. Reconcile final bars after market close and allow late vendor corrections.
- Use America/New_York for exchange/session display and UTC for storage, including DST, half-days, holidays.
- Replay/backtests request an `as_of_utc` cutoff; prohibit reading bars, finalized aggregates, revisions or corporate-action information unavailable by that cutoff. If historical `available_at` is unknown, label the run reconstructed/hindsight and do not claim strict point-in-time validity.

### 10.6 Minimum tests

- NY DST transitions, early closes, extended sessions, empty/no-trade minutes, missing minutes, late corrections, provider throttling, interrupted atomic writes, cache re-requests, partial candle finalization, 1m→5m boundary aggregation, and pinned dataset revisions.

## 11. Backtesting Specification (implementation contract)

### 11.1 Strategy authoring and isolation

- Support versioned Python strategies and visual-rule strategies through a typed signal/order-intent contract. Visual DSL is declarative; it must not execute arbitrary Python. Python strategy workers use isolated execution with resource limits and explicit file/network policy. Agent-generated strategy code requires user approval before execution.
- Strategy inputs: historical bars, approved indicators, time/calendar, portfolio state and explicit parameters. Strategy outputs: order intents with timestamp, symbol, side, quantity or sizing instruction, order type and optional limit/stop; all inputs/outputs logged.
- Distinguish indicators (observations) from strategies (trade rules) and portfolio/risk rules (sizing and constraints). Reuse Indicator Engine but do not grant strategies access to future values.

### 11.2 Deterministic event order and fill model

- Engine iterates through eligible time-ordered market events, computes signals only from information available at each decision point, validates risk/cash, then simulates orders. Default 1m MVP: signals on finalized bar close, market-order fill at next eligible bar open plus configurable slippage/fees; no same-bar close fill unless separately justified.
- Never infer intra-minute high/low traversal order from OHLC. When stop and target are both touched within a minute, apply an explicitly documented conservative ambiguity policy or mark indeterminate; do not select the profitable outcome by default.
- Fill model documents spread, liquidity/volume participation, partial fills, short availability, sessions, halts, corporate actions, fees, and FX where relevant. Unsupported realism is declared as a limitation, not fabricated. Start MVP with long-only US equities, one-currency USD, simple market orders and configurable costs; extend later.

### 11.3 Portfolio accounting and controls

- Track cash, open positions, realized/unrealized P&L, equity, commissions, slippage, exposure and rejection reasons per event. Use Decimal for accounting. Apply configurable initial capital, per-symbol sizing, max positions, session rules and risk constraints; never hard-code personal trading rules into engine defaults.
- Separate broker-verified trades, manual assertions, hypothetical chart scenarios and backtest fills in storage and UI. A backtest may overlay actual fills for comparison but never change actual account balances.

### 11.4 Reproducibility, bias controls, reports

- Pin strategy hash/version, parameter values, indicator versions, instrument universe and symbol history, market-data provider/revision/adjustment, coverage report, calendar, run window, timezone, engine version, cost/fill model and random seed if applicable.
- Report trade list, equity curve, net/gross return, maximum drawdown, win rate, average win/loss, profit factor, exposure and turnover; define denominators, handling of open positions, and benchmark. Provide breakdown by symbol/session/date when supported.
- Prevent lookahead; disclose survivorship bias (including delisted symbols), selection bias, data gaps, split/dividend adjustments, overfitting and multiple-testing risk. Include train/validation/out-of-sample or walk-forward workflows as later-stage capability.
- Store backtest runs as immutable results with status and failure logs. Re-running identical pinned inputs should yield identical results.

### 11.5 UI and API

- Backtest panel: choose strategy/version, symbols/universe, dates, timeframe, capital, fees/slippage, session scope and fill assumptions; run/cancel, inspect coverage and warnings before run.
- Results: equity/drawdown charts, metrics, trade table, and selectable backtest entry/exit overlays on chart using a visually distinct layer. Agent may critique runs using the same pinned evidence.
- Initial endpoints: `POST /v1/backtests/runs`, `GET /v1/backtests/runs/{id}`, `GET /v1/backtests/runs/{id}/results`, plus cancellation and strategy registry endpoints once contract is finalized. Long jobs must be queued, observable and cancellable.

### 11.6 Acceptance gates

- Simple deterministic fixture strategy yields expected next-bar fills, fees, cash and equity; repeated run produces identical results.
- Attempted future-data access is blocked; unfinished bar cannot trigger finalized-bar signal.
- Missing source intervals, unknown availability timestamps, split adjustments and ambiguous stop/target order produce explicit warnings or conservative handling.
- Backtest overlays do not modify or contaminate actual execution ledger or manual scenarios.

## 12. Multi-Source Data Platform

> v1.2에서 도입되어 v1.5에 통합된 요구사항이다.

### 12.1 Source classes and boundaries

- `PaidMarketDataAdapter`: licensed historical/live OHLCV, trades/ticks, reference and corporate-action data where contractually available.
- `BrokerMarketDataAdapter`: broker-provided quotes/bars where verified; never assume US intraday history, redistribution, or archival rights. Keep market-data permissions separate from `BrokerAccountAdapter` for private executions, positions and balances.
- `OfficialPublicDataAdapter`: SEC EDGAR, issuer IR, exchange/regulator publications, and other official public endpoints; prefer documented APIs/RSS/downloads to HTML scraping.
- `WebAcquisitionAdapter`: permitted HTML crawling for supplementary news, issuer updates and events. Respect site terms, robots guidance, rate limits, copyright, privacy and access controls. No CAPTCHA/paywall/login bypass; do not archive or redistribute restricted full-text content without rights.
- A single article or disclosure is a document/event record, **not** an OHLCV bar. Never infer precise market prices or historical data completeness from scraped web pages.

### 12.2 Source registry and provenance

Maintain a `DataSource` registry: source ID, operator, endpoint category, data types, entitlement/license and retention constraints, quote delay, granularity, supported sessions/history, adjustment convention, timezone, rate limits, last capability verification and health status. Unknown fields remain `unverified`.
Each ingested record carries `source_id`, `source_record_id` if present, `retrieved_at`, `published_at`/`event_at`/`available_at` when known, payload checksum or immutable reference, extraction/parser version, license policy, quality and revision. Protect credentials in OS secret storage; never log access tokens or account identifiers.

### 12.3 Routing, fallback and reconciliation

- Route by **data type + symbol + interval + required quality + entitlement**, not a global fixed provider priority. A designated canonical provider supplies each price series/segment; other sources serve verification or explicit fallback.
- Use cache-first verified coverage. For missing intervals, query eligible sources by capability, latency, quality and cost budget. A source switch creates a new provenance segment and a comparability warning; do not splice bars silently or mix adjusted and raw prices.
- When two providers disagree, preserve both source records, compute normalized comparison only after matching symbol, currency, session, timestamp boundary, adjustment and volume conventions, and flag material conflicts for review. No untraceable winner-takes-all overwrite.
- Broker-confirmed executions remain authoritative for their own account ledger; scraped statements or quote data cannot confirm broker fills.
- Collection jobs track request budget, rate limits, retries, backoff, circuit breaker, errors and checkpoint; prioritize active charts, pinned analysis/backtests, watchlist and historical backfill according to explicit queue policies.

### 12.4 Web content acquisition and point-in-time use

- Prefer structured official sources, then licensed feeds, then permitted crawl. Crawl on a reasonable per-domain schedule with conditional fetch (ETag/Last-Modified where supported), canonical URLs, content hashes, deduplication and parser failure alerts.
- Separate publication time from first time our system observed the content. A retrospective crawl of a past article does **not** prove the article was accessible at its claimed publication time. Backtests/agent replay must label reconstructed evidence and enforce strict point-in-time availability only when supported.
- Save minimal permitted metadata/excerpts, attribution and links; preserve source terms/retention policies. Scraped sentiment or inferred event classifications are interpretations, not primary facts.

### 12.5 Contracts and testing

New interfaces: `SourceCapabilities`, `SourceRecord`, `AcquisitionJob`, `CoverageManifest`, `SourceConflict`, `SourcePolicy`. Suggested endpoints: `GET /v1/data/sources`, `GET /v1/data/coverage`, `POST /v1/data/acquisition/jobs`, `GET /v1/data/conflicts` (names provisional).
Test source outages/fallback, conflicting OHLCV, adjustment/session mismatch, duplicate articles, publication-vs-observation times, restricted sources, rate limits, stale data, entitlement errors, cost caps and revoked credentials. No live source integration passes until current docs, access rights and sample payloads are verified.

### 12.6 Provider selection checklist (open decision)

Verify for each paid/broker/public provider: US equities and delisted coverage, minute-history depth, extended hours, correction handling, tick eligibility, latency, API quotas, historical bulk export, raw/adjusted semantics, redistribution/storage rights, monthly cost and failure modes. Do not claim a particular broker offers US minute bars without verification.

## 13. Provider Research & Trial Deployment

> v1.4 조사 결과를 2026-10-12에 v1.5 시스템 명세로 통합했다. 가격·기능은 가입 또는 구현 직전에 다시 검증한다.

**Full investigation:** `DATA_PROVIDER_EVALUATION_v1.5.md` is an evidence/research report, not guaranteed vendor entitlement. Vendor contract terms and real authenticated API outputs supersede estimates and public feature summaries.

### 13.1 Research outcomes

- Toss Securities OpenAPI officially supports `GET /api/v1/candles`, `interval=1m|1d`, maximum 200 bars per call, historical paging via `before`/`nextBefore`, adjusted flag. Its 2026-08-26 public launch removes a former uncertainty about mere API existence; depth, legal retention and execution detail remain account-specific verification tasks.
- KIS officially lists US/overseas minute-chart queries and account execution history; official Python sample `inquire_time_itemchartprice` (`HHDFS76950200`) requests up to 120 bars using `NMIN`, `PINC`, `NEXT`, `KEYB`. Effective historical reach and extended-session availability remain unproven.
- Kiwoom REST officially includes US-stock chart and account APIs. US-stock query limit is generally 5 req/s and 3 req/s during its published peak window, with additional group caps. Exact chart TR payload, coverage and retention require account testing.
- Mirae Asset Securities individual US-stock API access and supposed Mirae–Webull account interoperability are **not confirmed**. Webull has a distinct public API that requires its own approval/subscription; a Mirae DigitalX cryptocurrency API is NOT a US brokerage-stock API.
- Massive public stock plans: Starter $29/mo (5yr, 15-minute delayed), Developer $79/mo (10yr, 15-minute delayed, trades), Advanced $199/mo (20+yr, real-time, trades & quotes), and Basic free (2yr EOD). Alpaca is a second paid API candidate (Algo Trader Plus publicly listed at $99/mo; free Basic has IEX-only real-time coverage). Neither vendor is selected as historical or live default. (Superseded 2026-10-22: the user selected Alpaca as the default US provider, ADR 0035.) Databento US Equities is a specialist venue/dataset-dependent historical/tick/L2 candidate requiring metered cost/license investigation. Prices are as publicly displayed on verification date, not quotes.
- SEC provides public Submissions and CompanyFacts APIs and filing RSS, under declared User-Agent / fair access rules (max 10 req/s per published guidance). FINRA OTC weekly summary is delayed aggregate ATS/non-ATS volume—not live dark-pool order direction or live tick prints.

### 13.2 Provider implementation order and gating

1. Phase 1에서 정의할 `MarketDataProvider` protocol 뒤에 `TossMarketAdapter`, `KisMarketAdapter`, `KiwoomMarketAdapter`를 구현한다. 먼저 synthetic fixture로 protocol을 검증하며, `BrokerAccountAdapter`는 별도 계약과 읽기 전용 기본값을 유지한다. Phase 0에는 이 protocol의 계약 요구사항과 capability placeholder만 존재해도 정상이다.
2. Validate live account privileges and response shape **only after explicit user credential connection and read-only permission**. No order endpoint testing. Probe 1m max history, pagination, empty minutes, timestamp/session, adjustment policy, extended hours, price feed entitlements and legal local retention.
3. Retain **both `AlpacaMarketAdapter` and `MassiveMarketAdapter` as unranked paid candidates**. Implement the same `MarketDataProvider` contract and comparable fixtures/capability probes for each; do not choose a default paid vendor or hard-code separate historical-vs-live vendor ownership before PoC. `DatabentoMarketAdapter` remains a later venue-specific trades/order-book option. No procurement or subscription without explicit user approval.
4. Add `SecEdgarAdapter` and `FinraOtcWeeklyAdapter`; separately manage allowed `IssuerIRAdapter` and `PermittedNewsCrawler`. Model `ATSWeeklySummary` as a **weekly aggregate event** with publication lag, never a 1-minute bar or immediate buy/sell signal.
5. Store `verified_at`, `observed_entitlement`, `history_limit`, `source_feed_scope` (e.g., IEX vs SIP vs venue), `dataset_revision`, `billing_limit` and `rights_local_retention` per source capability. Unknown rights means block durable ingestion for uses requiring those rights until reviewed.
6. Compare vendor pricing and store a monthly budget policy (default _not set_; no API purchases), warn or refuse nonessential expensive jobs before cost exceeds user-configured caps. Subscription plans are alternatives, not cumulative requirements.

### 13.2A Confirmed sourcing decision (2026-10-12)

- **Alpaca and Massive are both retained as paid market-data candidates. On 2026-10-22 the user selected Alpaca as the default US provider (ADR 0035); Massive remains an alternative.** Historical 1m, intraday real-time, backtest history, feed coverage and rights are evaluation dimensions, **not reserved roles** assigned by vendor.
- Broker feeds, Alpaca, and Massive can be configured independently for eligible dataset categories. The routing policy is capability- and entitlement-driven, with an explicit canonical feed chosen **per symbol/time range/use case** after measured coverage/quality/rights/cost checks.
- Selection remains reversible. The PoC must run equivalent 1m and (if entitled) live-feed tests for both commercial providers, alongside read-only broker tests. Mark a provider as `not_tested` if credentials or licenses are unavailable; do not treat missing evidence as failure or prefer the other by default.
- Support one provider, different providers for distinct data classes, or an explicitly authorized dual-provider configuration. Compare cost using **non-additive scenarios**, and never silently splice historical price series or infer SIP breadth from IEX-only data.

### 13.3 New research acceptance conditions

- An authenticated US 1m fetch from each selected source is compared with at least one independent source after timestamp/session/adjustment alignment; preserve discrepancies as `SourceConflict` instead of silently rewriting.
- Provenance and entitlements follow data into caches, indicators, chart, replay, backtests, agent output and exports; no data source is silently upgraded from IEX/venue-only to consolidated coverage.
- Historical paging terminates correctly on no more data, pagination cursor loops, 429, missing intervals and daily session boundaries; never fill no-trade minutes with fabricated zero-volume prints.
- SEC/FINRA ingestion records `published_at`, `available_at` when demonstrable and `retrieved_at`; uncertainty is visible in hindsight analyses.
- Price/vendor capability matrix and paid pricing revalidated before an actual subscription, since product specifications can change.
