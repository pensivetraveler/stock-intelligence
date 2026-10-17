/* Generated from contracts.schema.json. Do not edit. */

export type StockIntelligenceWorkstationContractsV01 =
  | InstrumentRef
  | PriceBar
  | CoverageManifest
  | MarketBarsResponse
  | SourceCapabilities
  | SourceRecord
  | BrokerConfirmedExecution
  | ManualExecution
  | BacktestFill
  | ScenarioDefinition
  | IndicatorDefinition
  | AgentEvidence
  | BacktestRunConfig
  | SessionWindow
  | MarketInstrumentsResponse
  | ManualLedgerResponse
  | ScenarioLedgerResponse
  | ImportedExecution
  | ImportPreviewResponse
  | ImportBatch
  | ImportedLedgerResponse;
export type SchemaVersion = "0.1";
export type Identifier = string;
export type UtcTimestamp = string;
export type NonNegativeDecimalString = string;
export type Side = "buy" | "sell";
export type DecimalString = string;

export interface InstrumentRef {
  kind: "instrument_ref";
  schema_version: SchemaVersion;
  instrument_id: Identifier;
  symbol: string;
  venue: Identifier;
  asset_class: "us_equity";
  currency: "USD";
  identity_as_of: UtcTimestamp;
  identity_source: Identifier;
}
export interface PriceBar {
  kind: "price_bar";
  schema_version: SchemaVersion;
  instrument_id: Identifier;
  timeframe: "1m" | "5m" | "15m" | "30m" | "60m" | "1d";
  interval_start_utc: UtcTimestamp;
  interval_end_utc: UtcTimestamp;
  open: NonNegativeDecimalString;
  high: NonNegativeDecimalString;
  low: NonNegativeDecimalString;
  close: NonNegativeDecimalString;
  volume: NonNegativeDecimalString;
  volume_unit: "shares" | "provider_units";
  session: "pre" | "regular" | "post" | "closed" | "unknown";
  is_final: boolean;
  quality: "complete" | "partial" | "provisional" | "unknown";
  lineage: {
    kind: "provider" | "aggregated_1m";
    source_timeframe: "1m";
    expected_source_bar_count: number;
    observed_source_bar_count: number;
    source_dataset_revision: Identifier | null;
    calendar_id: Identifier;
    calendar_version: Identifier;
  };
  adjustment: "raw" | "split_adjusted" | "fully_adjusted" | "unknown";
  source_id: Identifier;
  source_feed_scope: Identifier;
  dataset_revision: Identifier;
  available_at_utc: UtcTimestamp;
}
export interface CoverageManifest {
  kind: "coverage_manifest";
  schema_version: SchemaVersion;
  instrument_id: Identifier;
  timeframe: "1m" | "5m" | "15m" | "30m" | "60m";
  requested_start_utc: UtcTimestamp;
  requested_end_utc: UtcTimestamp;
  coverage_status: "complete" | "partial" | "empty";
  verification_status: "unverified" | "verified";
  observed_interval_count: number;
  expected_interval_count: number;
  missing_intervals: {
    start_utc: UtcTimestamp;
    end_utc: UtcTimestamp;
    /**
     * fixture_missing: a synthetic fixture has no bar; not_available_as_of_cutoff: published after the cutoff; provider_no_bar: the provider reported no bar (normally no trades in that minute on the feed); outside_entitlement: the plan does not allow this range yet (e.g. the latest 15 minutes of SIP on a free plan); not_yet_final: too recent for an absent bar to be treated as no trades.
     */
    reason:
      | "fixture_missing"
      | "not_available_as_of_cutoff"
      | "provider_no_bar"
      | "outside_entitlement"
      | "not_yet_final";
  }[];
  source_id: Identifier;
  source_feed_scope: Identifier;
  adjustment: "raw" | "split_adjusted" | "fully_adjusted" | "unknown";
  dataset_revision: Identifier;
  point_in_time_cutoff_utc: UtcTimestamp | null;
}
export interface MarketBarsResponse {
  kind: "market_bars_response";
  schema_version: SchemaVersion;
  bars: PriceBar[];
  coverage: CoverageManifest;
}
export interface SourceCapabilities {
  kind: "source_capabilities";
  schema_version: SchemaVersion;
  source_id: Identifier;
  source_type:
    | "paid_market"
    | "broker_market"
    | "official_public"
    | "permitted_web"
    | "synthetic";
  verification_status: "unknown" | "documented" | "integration_tested";
  entitlement_status: "unknown" | "not_entitled" | "entitled";
  license_status: "unknown" | "not_licensed" | "licensed";
  feeds: string[];
  rights_local_retention: "unknown" | "prohibited" | "allowed";
  observed_at_utc: UtcTimestamp;
}
export interface SourceRecord {
  kind: "source_record";
  schema_version: SchemaVersion;
  source_id: Identifier;
  record_ref: Identifier;
  payload_sha256: string;
  event_at_utc?: UtcTimestamp;
  published_at_utc?: UtcTimestamp;
  available_at_utc?: UtcTimestamp;
  retrieved_at_utc: UtcTimestamp;
  parser_version: Identifier;
}
export interface BrokerConfirmedExecution {
  kind: "broker_confirmed_execution";
  schema_version: SchemaVersion;
  execution_id: Identifier;
  instrument_id: Identifier;
  side: Side;
  quantity: NonNegativeDecimalString;
  price: NonNegativeDecimalString;
  currency: "USD";
  executed_at_utc: UtcTimestamp;
  broker_id: Identifier;
  broker_account_ref: Identifier;
  broker_execution_id: Identifier;
  verification_status: "broker_confirmed";
  source_record_ref: Identifier;
  corrects_execution_id?: Identifier;
}
export interface ManualExecution {
  kind: "manual_execution";
  schema_version: SchemaVersion;
  execution_id: Identifier;
  instrument_id: Identifier;
  side: Side;
  quantity: NonNegativeDecimalString;
  price: NonNegativeDecimalString;
  currency: "USD";
  /**
   * When the server recorded the user's assertion.
   */
  asserted_at_utc: string;
  time_precision: "exact" | "minute" | "approximate" | "unknown";
  /**
   * Candle the user clicked: context only, never the execution time.
   */
  selected_interval_start_utc: string;
  selected_interval_end_utc: UtcTimestamp;
  evidence_status: "unverified" | "supported" | "reconciled";
  /**
   * User-claimed execution time; null only when time_precision is unknown. Independent of the selected interval.
   */
  executed_at_utc: UtcTimestamp | null;
  fees: NonNegativeDecimalString;
  memo: string;
}
export interface BacktestFill {
  kind: "backtest_fill";
  schema_version: SchemaVersion;
  fill_id: Identifier;
  run_id: Identifier;
  order_id: Identifier;
  instrument_id: Identifier;
  side: Side;
  quantity: NonNegativeDecimalString;
  price: NonNegativeDecimalString;
  currency: "USD";
  filled_at_utc: UtcTimestamp;
  fill_model_version: Identifier;
  fill_reason: Identifier;
}
export interface ScenarioDefinition {
  kind: "scenario_definition";
  schema_version: SchemaVersion;
  scenario_id: Identifier;
  version: number;
  instrument_id: Identifier;
  origin: "hypothetical";
  entry_price: NonNegativeDecimalString;
  exit_price: NonNegativeDecimalString;
  quantity: NonNegativeDecimalString;
  currency: "USD";
  /**
   * Total fees for the round trip.
   */
  fee_assumption: string;
  /**
   * Adverse price slippage per share, applied at entry and at exit.
   */
  slippage_assumption: string;
  direction: "long" | "short";
  entry_interval_start_utc: UtcTimestamp;
  exit_interval_start_utc: UtcTimestamp;
  created_at_utc: UtcTimestamp;
}
export interface IndicatorDefinition {
  kind: "indicator_definition";
  schema_version: SchemaVersion;
  indicator_id: Identifier;
  version: Identifier;
  authoring_type: "built_in" | "python" | "visual";
  parameters: {
    [k: string]: unknown;
  };
  warmup_bars: number;
  /**
   * @minItems 1
   */
  output_names: [Identifier, ...Identifier[]];
  point_in_time_policy: "finalized_only" | "partial_allowed_explicitly";
}
export interface AgentEvidence {
  kind: "agent_evidence";
  schema_version: SchemaVersion;
  analysis_id: Identifier;
  agent_version: Identifier;
  source_record_refs: Identifier[];
  dataset_revisions: Identifier[];
  cutoff_utc: UtcTimestamp;
  observations: string[];
  inferences: string[];
  uncertainty: string;
}
export interface BacktestRunConfig {
  kind: "backtest_run_config";
  schema_version: SchemaVersion;
  run_id: Identifier;
  dataset_revision: Identifier;
  strategy_id: Identifier;
  strategy_version: Identifier;
  start_utc: UtcTimestamp;
  end_utc: UtcTimestamp;
  initial_capital: NonNegativeDecimalString;
  currency: "USD";
  fill_policy: "next_eligible_bar_open";
  fee_assumption: NonNegativeDecimalString;
  slippage_assumption: NonNegativeDecimalString;
  seed: number;
}
export interface SessionWindow {
  kind: "session_window";
  schema_version: SchemaVersion;
  calendar_id: Identifier;
  session_date: string;
  is_trading_day: boolean;
  is_early_close: boolean;
  pre_open_utc: UtcTimestamp | null;
  regular_open_utc: UtcTimestamp | null;
  regular_close_utc: UtcTimestamp | null;
  post_close_utc: UtcTimestamp | null;
  calendar_version: Identifier;
  extended_hours_policy_version: Identifier;
}
export interface MarketInstrumentsResponse {
  kind: "market_instruments_response";
  schema_version: SchemaVersion;
  instruments: {
    instrument: InstrumentRef;
    source_id: Identifier;
    dataset_revision: Identifier;
    session_dates: string[];
  }[];
}
export interface ManualLedgerResponse {
  kind: "manual_ledger_response";
  schema_version: SchemaVersion;
  ledger: "manual_actual";
  executions: ManualExecution[];
  positions: LedgerPosition[];
}
export interface LedgerPosition {
  instrument_id: Identifier;
  open_quantity: DecimalString;
  average_cost: NonNegativeDecimalString | null;
  realized_pnl: DecimalString;
  fees: NonNegativeDecimalString;
  unordered_execution_ids: Identifier[];
}
export interface ScenarioLedgerResponse {
  kind: "scenario_ledger_response";
  schema_version: SchemaVersion;
  ledger: "hypothetical";
  scenarios: {
    scenario: ScenarioDefinition;
    gross_pnl: DecimalString;
    costs: NonNegativeDecimalString;
    net_pnl: DecimalString;
  }[];
}
/**
 * A fill read from a user-supplied broker statement file. Not API-verified.
 */
export interface ImportedExecution {
  kind: "imported_execution";
  schema_version: SchemaVersion;
  execution_id: Identifier;
  instrument_id: Identifier;
  side: Side;
  quantity: NonNegativeDecimalString;
  price: NonNegativeDecimalString;
  fees: NonNegativeDecimalString;
  currency: "USD";
  executed_at_utc: UtcTimestamp;
  time_precision: "exact" | "minute";
  broker_label: string;
  broker_execution_id: string | null;
  source: {
    batch_id: Identifier;
    file_sha256: string;
    row_number: number;
  };
  verification_status: "statement_imported";
  duplicate_review: {
    decision: "none" | "imported_after_review";
    related: string[];
  };
}
export interface ImportPreviewResponse {
  kind: "import_preview_response";
  schema_version: SchemaVersion;
  batch_id: Identifier;
  file_name: string;
  file_sha256: string;
  broker_label: string;
  columns: string[];
  mapping: {
    executed_at: string | null;
    date: string | null;
    time: string | null;
    symbol: string | null;
    side: string | null;
    quantity: string | null;
    price: string | null;
    fees: string | null;
    broker_execution_id: string | null;
  };
  timestamp_timezone: "America/New_York" | "UTC";
  already_imported: boolean;
  rows: {
    row_number: number;
    status: "new" | "duplicate_exact" | "possible_duplicate" | "invalid";
    execution: ImportedExecution | null;
    errors: string[];
    matches: string[];
  }[];
  summary: {
    new: number;
    duplicate_exact: number;
    possible_duplicate: number;
    invalid: number;
  };
}
export interface ImportBatch {
  kind: "import_batch";
  schema_version: SchemaVersion;
  batch_id: Identifier;
  file_name: string;
  file_sha256: string;
  broker_label: string;
  committed_at_utc: UtcTimestamp;
  row_count: number;
  imported_execution_ids: Identifier[];
  skipped_rows: {
    row_number: number;
    reason: "invalid" | "duplicate_exact" | "skipped_after_review";
  }[];
}
export interface ImportedLedgerResponse {
  kind: "imported_ledger_response";
  schema_version: SchemaVersion;
  ledger: "statement_imported";
  executions: ImportedExecution[];
  positions: LedgerPosition[];
  batches: ImportBatch[];
}
