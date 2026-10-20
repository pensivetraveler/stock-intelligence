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
  | ImportedLedgerResponse
  | CredentialSummary
  | CredentialListResponse
  | SessionListResponse
  | SymbolSearchResponse
  | MarketQuote
  | StockInfo
  | ExchangeRate
  | BrokerMarketCalendar
  | ResearchTable
  | BrokerOrderExecution
  | BrokerLedgerResponse
  | BrokerSyncResult
  | BrokerAccountSnapshot
  | BrokerSellableQuantity
  | RealtimeEvent
  | TickListResponse
  | BacktestStrategy
  | BacktestStrategyListResponse
  | BacktestRun
  | BacktestRunListResponse
  | BacktestResults;
export type SchemaVersion = "0.1";
export type Identifier = string;
export type UtcTimestamp = string;
export type NonNegativeDecimalString = string;
export type Side = "buy" | "sell";
export type DecimalString = string;
/**
 * One event relayed from the broker's realtime stream to the app. Trade and order book frames can be dropped by the broker (no sequence numbers); order events are lossless only within one connection.
 */
export type RealtimeEvent = {
  kind: "realtime_event";
  [k: string]: unknown;
} & RealtimeEvent1;
export type RealtimeEvent1 =
  | {
      kind: "realtime_event";
      schema_version: SchemaVersion;
      event: "trade";
      source_id: Identifier;
      symbol: string;
      instrument_id: null | Identifier;
      price: NonNegativeDecimalString;
      volume: NonNegativeDecimalString;
      currency: string;
      exchange_time_utc: UtcTimestamp;
      received_at_utc: UtcTimestamp;
    }
  | {
      kind: "realtime_event";
      schema_version: SchemaVersion;
      event: "orderbook";
      source_id: Identifier;
      symbol: string;
      instrument_id: null | Identifier;
      currency: string;
      exchange_time_utc: UtcTimestamp;
      asks: {
        price: NonNegativeDecimalString;
        volume: NonNegativeDecimalString;
      }[];
      bids: {
        price: NonNegativeDecimalString;
        volume: NonNegativeDecimalString;
      }[];
    }
  | {
      kind: "realtime_event";
      schema_version: SchemaVersion;
      event: "order";
      broker_id: "toss";
      account_ref: Identifier;
      order_event: string;
      symbol: string;
      side: string;
      status: string;
      filled_quantity: null | NonNegativeDecimalString;
    }
  | {
      kind: "realtime_event";
      schema_version: SchemaVersion;
      event: "ledger_synced";
      stored_new: number;
      conflicts: number;
    }
  | {
      kind: "realtime_event";
      schema_version: SchemaVersion;
      event: "status";
      connected: boolean;
      detail: null | string;
      subscribed: string[];
      rejected: string[];
    };

export interface InstrumentRef {
  kind: "instrument_ref";
  schema_version: SchemaVersion;
  instrument_id: Identifier;
  symbol: string;
  venue: Identifier;
  asset_class: "us_equity" | "kr_equity";
  currency: "USD" | "KRW";
  identity_as_of: UtcTimestamp;
  identity_source: Identifier;
}
export interface PriceBar {
  kind: "price_bar";
  schema_version: SchemaVersion;
  instrument_id: Identifier;
  timeframe: string;
  interval_start_utc: UtcTimestamp;
  interval_end_utc: UtcTimestamp;
  open: NonNegativeDecimalString;
  high: NonNegativeDecimalString;
  low: NonNegativeDecimalString;
  close: NonNegativeDecimalString;
  volume: NonNegativeDecimalString;
  volume_unit: "shares" | "provider_units";
  /**
   * all_day: a daily or weekly bar spanning the whole trading day, extended sessions included (Toss Korean daily candles, 08:00-20:00 KST).
   */
  session: "pre" | "regular" | "post" | "all_day" | "closed" | "unknown";
  is_final: boolean;
  quality: "complete" | "partial" | "provisional" | "unknown";
  lineage: {
    kind: "provider" | "aggregated_1m" | "aggregated_1d";
    source_timeframe: "1m" | "1d";
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
  timeframe: string;
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
     * fixture_missing: a synthetic fixture has no bar; not_available_as_of_cutoff: published after the cutoff; provider_no_bar: the provider reported no bar (normally no trades in that minute on the feed); outside_entitlement: the plan does not allow this range yet (e.g. the latest 15 minutes of SIP on a free plan); not_yet_final: too recent for an absent bar to be treated as no trades. session_not_covered: the source does not provide this session (e.g. a broker feed confirmed for regular hours only). beyond_source_history: older than this source can serve (e.g. a broker returning only the latest 40 bars).
     */
    reason:
      | "fixture_missing"
      | "not_available_as_of_cutoff"
      | "provider_no_bar"
      | "outside_entitlement"
      | "not_yet_final"
      | "session_not_covered"
      | "beyond_source_history";
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
  /**
   * USD for US listings, KRW for Korean (kr:) listings.
   */
  currency: "USD" | "KRW";
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
  /**
   * USD for US listings, KRW for Korean (kr:) listings.
   */
  currency: "USD" | "KRW";
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
  currency: "USD" | "KRW";
  filled_at_utc: UtcTimestamp;
  fill_model_version: Identifier;
  fill_reason: Identifier;
  fee?: NonNegativeDecimalString;
  /**
   * @maxItems 10
   */
  notes?:
    | []
    | [Identifier]
    | [Identifier, Identifier]
    | [Identifier, Identifier, Identifier]
    | [Identifier, Identifier, Identifier, Identifier]
    | [Identifier, Identifier, Identifier, Identifier, Identifier]
    | [Identifier, Identifier, Identifier, Identifier, Identifier, Identifier]
    | [
        Identifier,
        Identifier,
        Identifier,
        Identifier,
        Identifier,
        Identifier,
        Identifier,
      ]
    | [
        Identifier,
        Identifier,
        Identifier,
        Identifier,
        Identifier,
        Identifier,
        Identifier,
        Identifier,
      ]
    | [
        Identifier,
        Identifier,
        Identifier,
        Identifier,
        Identifier,
        Identifier,
        Identifier,
        Identifier,
        Identifier,
      ]
    | [
        Identifier,
        Identifier,
        Identifier,
        Identifier,
        Identifier,
        Identifier,
        Identifier,
        Identifier,
        Identifier,
        Identifier,
      ];
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
  /**
   * USD for US listings, KRW for Korean (kr:) listings.
   */
  currency: "USD" | "KRW";
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
  currency: "USD" | "KRW";
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
  /**
   * USD for US listings, KRW for Korean (kr:) listings.
   */
  currency: "USD" | "KRW";
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
  timestamp_timezone: "America/New_York" | "Asia/Seoul" | "UTC";
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
/**
 * A registered provider key as shown to the user. Secret values and full account numbers are never part of this contract.
 */
export interface CredentialSummary {
  kind: "credential_summary";
  schema_version: SchemaVersion;
  credential_id: string;
  provider: "alpaca" | "kis" | "toss" | "kiwoom" | "meritz";
  label: string;
  account_suffix: string | null;
  /**
   * Whether a broker key belongs to a real (실전) or a demo (모의투자) account; null for vendor keys, which are not per account.
   */
  environment: "real" | "demo" | null;
  priority: number;
  enabled: boolean;
  /**
   * The key's provider is the running market-data provider.
   */
  active: boolean;
  created_at_utc: UtcTimestamp;
  updated_at_utc: UtcTimestamp;
  status: null | {
    state: "idle" | "ok" | "cooling" | "error";
    cooling_until_utc: UtcTimestamp | null;
    last_error: string | null;
    last_used_at_utc: UtcTimestamp | null;
  };
}
export interface CredentialListResponse {
  kind: "credential_list_response";
  schema_version: SchemaVersion;
  active_provider: "fake" | "alpaca" | "kis" | "toss" | "kiwoom" | "meritz";
  providers: {
    provider: "alpaca" | "kis" | "toss" | "kiwoom" | "meritz";
    display_name: string;
    per_account: boolean;
    /**
     * @minItems 1
     */
    fields: [
      {
        name: string;
        label: string;
      },
      ...{
        name: string;
        label: string;
      }[],
    ];
  }[];
  credentials: CredentialSummary[];
}
/**
 * Consecutive trading sessions ending at end_date (inclusive), oldest first.
 */
export interface SessionListResponse {
  kind: "session_list_response";
  schema_version: SchemaVersion;
  calendar_id: "XNAS" | "XNYS" | "XKRX" | "XKOS";
  end_date: string;
  /**
   * @maxItems 120
   */
  sessions: SessionWindow[];
}
/**
 * US-listed stocks matching a ticker or company-name query, from the provider's symbol directory. Any result can be charted with the active provider.
 */
export interface SymbolSearchResponse {
  kind: "symbol_search_response";
  schema_version: SchemaVersion;
  query: string;
  source_id: Identifier;
  /**
   * When the directory was last fetched from the provider.
   */
  fetched_at_utc: string;
  /**
   * The directory is older than a day and could not be refreshed; results may miss new listings.
   */
  stale: boolean;
  /**
   * @maxItems 50
   */
  results: {
    listing: {
      instrument: InstrumentRef;
      source_id: Identifier;
      dataset_revision: Identifier;
      session_dates: string[];
    };
    name: string;
    /**
     * Korean display name from Toss, when a Toss key is registered and Toss has one.
     */
    local_name: null | string;
  }[];
}
/**
 * A point-in-time quote read on demand from a broker: last price, order book, recent trades and daily price limits. Not stored; prices are exact decimal strings and times UTC.
 */
export interface MarketQuote {
  kind: "market_quote";
  schema_version: SchemaVersion;
  instrument_id: Identifier;
  symbol: string;
  source_id: Identifier;
  currency: "USD" | "KRW";
  /**
   * When this API read the quote.
   */
  observed_at_utc: string;
  last: null | {
    price: DecimalString;
    at_utc: UtcTimestamp;
  };
  orderbook: null | {
    at_utc: UtcTimestamp;
    asks: {
      price: DecimalString;
      volume: NonNegativeDecimalString;
    }[];
    bids: {
      price: DecimalString;
      volume: NonNegativeDecimalString;
    }[];
  };
  /**
   * @maxItems 50
   */
  trades: {
    price: DecimalString;
    volume: NonNegativeDecimalString;
    at_utc: UtcTimestamp;
  }[];
  price_limits: null | {
    upper: DecimalString;
    lower: DecimalString;
    at_utc: UtcTimestamp;
  };
}
/**
 * A broker's reference data for one stock and its active buying warnings, read on demand.
 */
export interface StockInfo {
  kind: "stock_info";
  schema_version: SchemaVersion;
  instrument_id: Identifier;
  symbol: string;
  source_id: Identifier;
  observed_at_utc: UtcTimestamp;
  name: null | string;
  english_name: null | string;
  isin: null | string;
  market: null | string;
  security_type: null | string;
  is_common_share: null | boolean;
  status: null | string;
  currency: null | string;
  list_date: null | string;
  delist_date: null | string;
  shares_outstanding: null | NonNegativeDecimalString;
  leverage_factor: null | DecimalString;
  warnings: {
    type: string;
    exchange: null | string;
    start_date: null | string;
    end_date: null | string;
  }[];
}
/**
 * A broker's reference (display) exchange rate for one minute-long window; actual trades may use a different rate.
 */
export interface ExchangeRate {
  kind: "exchange_rate";
  schema_version: SchemaVersion;
  source_id: Identifier;
  observed_at_utc: UtcTimestamp;
  base_currency: "USD" | "KRW";
  quote_currency: "USD" | "KRW";
  rate: NonNegativeDecimalString;
  mid_rate: null | NonNegativeDecimalString;
  basis_point: null | DecimalString;
  change: null | string;
  valid_from_utc: UtcTimestamp;
  valid_until_utc: UtcTimestamp;
}
/**
 * A broker's market hours for the previous, current and next business day, compared with the local exchange calendar where one exists. Closed sessions are absent.
 */
export interface BrokerMarketCalendar {
  kind: "broker_market_calendar";
  schema_version: SchemaVersion;
  source_id: Identifier;
  market: "US" | "KR";
  observed_at_utc: UtcTimestamp;
  /**
   * @maxItems 3
   */
  days:
    | []
    | [
        {
          role: "previous" | "today" | "next";
          date: string;
          sessions: {
            name: "day_market" | "pre_market" | "regular" | "after_market";
            start_utc: UtcTimestamp;
            end_utc: UtcTimestamp;
            local_start_utc: null | UtcTimestamp;
            local_end_utc: null | UtcTimestamp;
            /**
             * Whether the local calendar the charts use has the same bounds; null when there is no local session to compare.
             */
            agrees: null | boolean;
          }[];
        },
      ]
    | [
        {
          role: "previous" | "today" | "next";
          date: string;
          sessions: {
            name: "day_market" | "pre_market" | "regular" | "after_market";
            start_utc: UtcTimestamp;
            end_utc: UtcTimestamp;
            local_start_utc: null | UtcTimestamp;
            local_end_utc: null | UtcTimestamp;
            /**
             * Whether the local calendar the charts use has the same bounds; null when there is no local session to compare.
             */
            agrees: null | boolean;
          }[];
        },
        {
          role: "previous" | "today" | "next";
          date: string;
          sessions: {
            name: "day_market" | "pre_market" | "regular" | "after_market";
            start_utc: UtcTimestamp;
            end_utc: UtcTimestamp;
            local_start_utc: null | UtcTimestamp;
            local_end_utc: null | UtcTimestamp;
            /**
             * Whether the local calendar the charts use has the same bounds; null when there is no local session to compare.
             */
            agrees: null | boolean;
          }[];
        },
      ]
    | [
        {
          role: "previous" | "today" | "next";
          date: string;
          sessions: {
            name: "day_market" | "pre_market" | "regular" | "after_market";
            start_utc: UtcTimestamp;
            end_utc: UtcTimestamp;
            local_start_utc: null | UtcTimestamp;
            local_end_utc: null | UtcTimestamp;
            /**
             * Whether the local calendar the charts use has the same bounds; null when there is no local session to compare.
             */
            agrees: null | boolean;
          }[];
        },
        {
          role: "previous" | "today" | "next";
          date: string;
          sessions: {
            name: "day_market" | "pre_market" | "regular" | "after_market";
            start_utc: UtcTimestamp;
            end_utc: UtcTimestamp;
            local_start_utc: null | UtcTimestamp;
            local_end_utc: null | UtcTimestamp;
            /**
             * Whether the local calendar the charts use has the same bounds; null when there is no local session to compare.
             */
            agrees: null | boolean;
          }[];
        },
        {
          role: "previous" | "today" | "next";
          date: string;
          sessions: {
            name: "day_market" | "pre_market" | "regular" | "after_market";
            start_utc: UtcTimestamp;
            end_utc: UtcTimestamp;
            local_start_utc: null | UtcTimestamp;
            local_end_utc: null | UtcTimestamp;
            /**
             * Whether the local calendar the charts use has the same bounds; null when there is no local session to compare.
             */
            agrees: null | boolean;
          }[];
        },
      ];
}
/**
 * A broker research table for reading (rankings, market indicators, sectors, Korean trading trends). Values are exact strings as the broker sent them, times are UTC, nested fields are dotted column names. Not an input to charts, indicators or backtests.
 */
export interface ResearchTable {
  kind: "research_table";
  schema_version: SchemaVersion;
  source_id: Identifier;
  dataset:
    | "rankings"
    | "market_indicator_prices"
    | "market_indicator_candles"
    | "market_indicator_investor_trading"
    | "sectors"
    | "sector_rankings"
    | "sector"
    | "sector_stocks"
    | "sector_etfs"
    | "stock_investor_trading"
    | "stock_program_trades"
    | "stock_short_selling"
    | "stock_credit_trades"
    | "stock_securities_lending";
  /**
   * The symbol or sector ID the table is about, if any.
   */
  key: null | string;
  params: {
    [k: string]: string;
  };
  observed_at_utc: UtcTimestamp;
  as_of_utc: null | UtcTimestamp;
  /**
   * Value to pass as `until` or `before` for older rows.
   */
  next_cursor: null | string;
  /**
   * @maxItems 80
   */
  columns: string[];
  /**
   * @maxItems 2000
   */
  rows: (null | boolean | string)[][];
}
/**
 * An order's execution as reported by a broker API, read on demand and stored append-only. A separate ledger from manual entries and statement imports; never summed with them.
 */
export interface BrokerOrderExecution {
  kind: "broker_order_execution";
  schema_version: SchemaVersion;
  execution_id: Identifier;
  instrument_id: Identifier;
  symbol: string;
  side: Side;
  quantity: NonNegativeDecimalString;
  price: NonNegativeDecimalString;
  fees: NonNegativeDecimalString;
  commission: NonNegativeDecimalString;
  tax: NonNegativeDecimalString;
  /**
   * USD for US listings, KRW for Korean (kr:) listings.
   */
  currency: "USD" | "KRW";
  executed_at_utc: null | UtcTimestamp;
  /**
   * The broker's filledAt for the order: when its last fill happened, as reported. Not a per-fill time.
   */
  time_meaning: "reported_last_fill";
  /**
   * One record per order: total filled quantity at the average fill price. The broker reports no individual fills.
   */
  granularity: "order_aggregate";
  order_type: string;
  order_status: string;
  ordered_at_utc: UtcTimestamp;
  settlement_date: null | string;
  broker_id: "toss";
  broker_account_ref: Identifier;
  broker_order_id: string;
  verification_status: "broker_api_reported";
  synced_at_utc: UtcTimestamp;
}
export interface BrokerLedgerResponse {
  kind: "broker_ledger_response";
  schema_version: SchemaVersion;
  ledger: "broker_api";
  executions: BrokerOrderExecution[];
  positions: LedgerPosition[];
}
/**
 * What one on-demand sync of closed broker orders stored.
 */
export interface BrokerSyncResult {
  kind: "broker_sync_result";
  schema_version: SchemaVersion;
  broker_id: "toss";
  synced_at_utc: UtcTimestamp;
  accounts: number;
  orders_seen: number;
  stored_new: number;
  already_stored: number;
  skipped: {
    not_filled: number;
    unsupported_currency: number;
    unknown_instrument: number;
  };
  /**
   * Broker order IDs whose reported execution changed after it was stored; the stored record is kept.
   */
  conflicts: string[];
}
/**
 * A broker account view read on demand: holdings, buying power, commission rates and open orders. Account numbers appear only as their last four digits. Nothing here is stored.
 */
export interface BrokerAccountSnapshot {
  kind: "broker_account_snapshot";
  schema_version: SchemaVersion;
  broker_id: "toss";
  observed_at_utc: UtcTimestamp;
  accounts: {
    account_ref: Identifier;
    account_seq: number;
    account_suffix: null | string;
    account_type: string;
    summary: null | {
      purchase_amount: {
        krw: null | DecimalString;
        usd: null | DecimalString;
      };
      market_value: {
        krw: null | DecimalString;
        usd: null | DecimalString;
      };
      profit_loss: {
        krw: null | DecimalString;
        usd: null | DecimalString;
      };
      profit_loss_rate: null | DecimalString;
      daily_profit_loss: {
        krw: null | DecimalString;
        usd: null | DecimalString;
      };
      daily_profit_loss_rate: null | DecimalString;
    };
    holdings: {
      symbol: string;
      name: null | string;
      market_country: null | string;
      currency: null | string;
      quantity: null | NonNegativeDecimalString;
      last_price: null | DecimalString;
      average_purchase_price: null | DecimalString;
      purchase_amount: null | DecimalString;
      market_value: null | DecimalString;
      profit_loss: null | DecimalString;
      profit_loss_rate: null | DecimalString;
      daily_profit_loss: null | DecimalString;
      commission: null | DecimalString;
      tax: null | DecimalString;
    }[];
    buying_power: {
      usd: null | DecimalString;
      krw: null | DecimalString;
    };
    commission_rates: {
      market_country: string;
      rate: null | DecimalString;
      start_date: null | string;
      end_date: null | string;
    }[];
    open_orders: {
      broker_order_id: string;
      symbol: string;
      side: string;
      order_type: null | string;
      status: string;
      price: null | DecimalString;
      quantity: null | NonNegativeDecimalString;
      filled_quantity: null | NonNegativeDecimalString;
      currency: null | string;
      ordered_at_utc: null | UtcTimestamp;
    }[];
    open_conditional_orders: {
      conditional_order_id: string;
      type: string;
      status: string;
      symbol: string;
      quantity: null | NonNegativeDecimalString;
      expire_date: null | string;
      created_at_utc: null | UtcTimestamp;
    }[];
    /**
     * Parts of this account that could not be read; the rest is shown.
     */
    errors: string[];
  }[];
}
/**
 * How much of one stock a broker account can sell now, read on demand.
 */
export interface BrokerSellableQuantity {
  kind: "broker_sellable_quantity";
  schema_version: SchemaVersion;
  broker_id: "toss";
  account_ref: Identifier;
  symbol: string;
  sellable_quantity: NonNegativeDecimalString;
  observed_at_utc: UtcTimestamp;
}
/**
 * Trade ticks this app received from a broker stream for one symbol and New York date. Lossy by nature: a received record, never a complete tape, and never an input to bars.
 */
export interface TickListResponse {
  kind: "tick_list_response";
  schema_version: SchemaVersion;
  source_id: Identifier;
  symbol: string;
  session_date: string;
  completeness: "lossy_stream";
  /**
   * All stored ticks for the day; `ticks` may hold only the latest of them.
   */
  count: number;
  truncated: boolean;
  /**
   * @maxItems 5000
   */
  ticks: {
    price: NonNegativeDecimalString;
    volume: NonNegativeDecimalString;
    currency: string;
    exchange_time_utc: UtcTimestamp;
    received_at_utc: UtcTimestamp;
  }[];
}
export interface BacktestStrategy {
  kind: "backtest_strategy";
  schema_version: SchemaVersion;
  strategy_id: Identifier;
  name: string;
  version: number;
  strategy_type: "rules" | "python";
  definition: {
    type: "rules" | "python";
    [k: string]: unknown;
  };
  definition_sha256: string;
  created_at_utc: UtcTimestamp;
  versions?: number[];
}
export interface BacktestStrategyListResponse {
  kind: "backtest_strategy_list_response";
  schema_version: SchemaVersion;
  strategies: BacktestStrategy[];
}
export interface BacktestRun {
  kind: "backtest_run";
  schema_version: SchemaVersion;
  run_id: Identifier;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  progress: number;
  strategy_id: Identifier;
  strategy_version: number;
  strategy_sha256: string;
  config: {
    /**
     * @minItems 1
     * @maxItems 20
     */
    instrument_ids:
      | [Identifier]
      | [Identifier, Identifier]
      | [Identifier, Identifier, Identifier]
      | [Identifier, Identifier, Identifier, Identifier]
      | [Identifier, Identifier, Identifier, Identifier, Identifier]
      | [Identifier, Identifier, Identifier, Identifier, Identifier, Identifier]
      | [
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
        ]
      | [
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
        ]
      | [
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
        ]
      | [
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
        ]
      | [
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
        ]
      | [
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
        ]
      | [
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
        ]
      | [
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
        ]
      | [
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
        ]
      | [
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
        ]
      | [
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
        ]
      | [
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
        ]
      | [
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
        ]
      | [
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
          Identifier,
        ];
    timeframe: string;
    start_date: string;
    end_date: string;
    initial_capital: NonNegativeDecimalString;
    currency: "USD" | "KRW";
    fee_rate: NonNegativeDecimalString;
    fee_per_order: NonNegativeDecimalString;
    slippage_bps: NonNegativeDecimalString;
    fill_policy: "next_eligible_bar_open";
    as_of_utc: UtcTimestamp | null;
    seed: number;
    adjustment?: "raw" | "split_adjusted" | "fully_adjusted";
  };
  created_at_utc: UtcTimestamp;
  started_at_utc: UtcTimestamp | null;
  finished_at_utc: UtcTimestamp | null;
  error: string | null;
  summary: {
    final_equity: DecimalString;
    net_return: DecimalString | null;
    max_drawdown: DecimalString | null;
    trade_count: number;
    benchmark_return: DecimalString | null;
    warning_count: number;
  } | null;
}
export interface BacktestRunListResponse {
  kind: "backtest_run_list_response";
  schema_version: SchemaVersion;
  runs: BacktestRun[];
}
export interface BacktestResults {
  kind: "backtest_results";
  schema_version: SchemaVersion;
  run_id: Identifier;
  engine_version: Identifier;
  fill_model_version: Identifier;
  currency: "USD" | "KRW";
  initial_capital: NonNegativeDecimalString;
  final_equity: DecimalString;
  metrics: {
    net_return: DecimalString | null;
    gross_return: DecimalString | null;
    max_drawdown: DecimalString | null;
    win_rate: DecimalString | null;
    profit_factor: DecimalString | null;
    exposure: DecimalString | null;
    turnover: DecimalString | null;
    benchmark_return: DecimalString | null;
    average_win: DecimalString | null;
    average_loss: DecimalString | null;
    fees: NonNegativeDecimalString;
    slippage_cost: NonNegativeDecimalString;
    trade_count: number;
  };
  metric_definitions: {
    [k: string]: string;
  };
  equity_curve: {
    time_utc: UtcTimestamp;
    equity: DecimalString;
    cash: DecimalString;
    drawdown: NonNegativeDecimalString;
    benchmark_equity: DecimalString;
  }[];
  orders: {
    order_id: Identifier;
    instrument_id: Identifier;
    side: Side;
    signal_utc: UtcTimestamp;
    status: "queued" | "filled" | "rejected" | "unfilled_at_end";
    reason: Identifier | null;
    filled_at_utc: UtcTimestamp | null;
  }[];
  fills: BacktestFill[];
  trades: {
    instrument_id: Identifier;
    entry_order_id: Identifier;
    entry_utc: UtcTimestamp;
    entry_price: NonNegativeDecimalString;
    quantity: NonNegativeDecimalString;
    exit_utc: UtcTimestamp | null;
    exit_price: NonNegativeDecimalString | null;
    exit_reason: "signal" | "stop_loss" | "take_profit" | "open_at_end";
    open_quantity: NonNegativeDecimalString;
    net_pnl: DecimalString;
    return: DecimalString | null;
  }[];
  per_symbol: {
    instrument_id: Identifier;
    trade_count: number;
    win_rate: DecimalString | null;
    realized_pnl: DecimalString;
    unrealized_pnl: DecimalString;
  }[];
  inputs: {
    instrument_id: Identifier;
    timeframe: string;
    calendar_id: Identifier;
    calendar_version: Identifier;
    requested_start_utc: UtcTimestamp;
    requested_end_utc: UtcTimestamp;
    as_of_utc: UtcTimestamp | null;
    source_ids: Identifier[];
    feed_scopes: Identifier[];
    adjustments: Identifier[];
    dataset_revisions: Identifier[];
    bar_count: number;
    expected_interval_count: number;
    missing_interval_count: number;
    /**
     * @maxItems 50
     */
    missing_intervals: {
      start_utc: UtcTimestamp;
      end_utc: UtcTimestamp;
      reason: Identifier;
    }[];
    non_final_bar_count: number;
    bars_sha256: string;
    suspected_split_dates: string[];
    adjustment_requested?: "raw" | "split_adjusted" | "fully_adjusted";
  }[];
  warnings: string[];
  /**
   * @maxItems 200
   */
  logs: string[];
  warning_details?: {
    code:
      | "missing_intervals"
      | "non_final_bars"
      | "raw_prices"
      | "suspected_splits"
      | "no_bars"
      | "survivorship"
      | "adjusted_prices";
    instrument_id: Identifier | null;
    count: number | null;
    dates: string[];
  }[];
}
