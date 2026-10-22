import type {
  BrokerAccountSnapshot,
  BrokerLedgerResponse,
  BrokerMarketCalendar,
  BrokerSellableQuantity,
  BrokerSyncResult,
  ExchangeRate,
  ImportBatch,
  ImportedLedgerResponse,
  ImportPreviewResponse,
  ManualExecution,
  ManualLedgerResponse,
  MarketBarsResponse,
  MarketQuote,
  RealtimeEvent,
  ResearchTable,
  MarketInstrumentsResponse,
  ScenarioDefinition,
  ScenarioLedgerResponse,
  SessionListResponse,
  SessionWindow,
  StockInfo,
  SymbolSearchResponse,
  ReconciliationRecord,
  TradeReconciliationResponse,
} from "@siw/contracts/types";

import { zoneOf } from "./market-zone";
import { openRealtime, type RealtimeSocket } from "./realtime";

/** Whole minutes written "Nm" (see timeframes.ts); the API aggregates any of them. */
export type Timeframe = string;
export type InstrumentListing =
  MarketInstrumentsResponse["instruments"][number];
export type SymbolSearchResult = SymbolSearchResponse["results"][number];

/** A failed API call, carrying the API's error envelope when one was returned. */
export class MarketApiError extends Error {
  constructor(
    message: string,
    readonly code: string,
    readonly requestId: string | null = null,
  ) {
    super(message);
    this.name = "MarketApiError";
  }
}

/**
 * Regular-session snapshot for the watchlist: the first open and the latest close of
 * the session, from 60m bars. `null` when the day has no regular-session bars.
 */
export interface SessionQuote {
  instrumentId: string;
  sessionDate: string;
  open: string;
  last: string;
  lastFinal: boolean;
}

/** Filters of the read-only reconciliation; days are each exchange's own dates. */
export interface ReconciliationQuery {
  instrumentId?: string;
  fromDate?: string;
  toDate?: string;
  status?: ReconciliationRecord["status"];
  limit?: number;
}

export interface MarketApi {
  instruments(): Promise<InstrumentListing[]>;
  /** A broker research table (rankings, indicators, sectors, Korean trading trends). */
  research(
    dataset: string,
    key: string | null,
    params: Record<string, string>,
  ): Promise<ResearchTable>;
  /** The app's relay of the broker's realtime stream. */
  openRealtime(onEvent: (event: RealtimeEvent) => void): RealtimeSocket;
  /** Broker reference data and active buying warnings for one stock. */
  stockInfo(instrumentId: string): Promise<StockInfo>;
  /** The broker's USD/KRW reference rate. */
  exchangeRate(): Promise<ExchangeRate>;
  /** The broker's market hours around today, compared with the local calendar. */
  brokerCalendar(market: "US" | "KR"): Promise<BrokerMarketCalendar>;
  /** Live last price, order book, recent trades and price limits from the broker. */
  liveQuote(instrumentId: string): Promise<MarketQuote>;
  /** US-listed stocks whose ticker or company name matches `query`, tickers first. */
  searchSymbols(query: string, limit?: number): Promise<SymbolSearchResponse>;
  quote(
    instrumentId: string,
    sessionDate: string,
  ): Promise<SessionQuote | null>;
  /** The latest `count` trading sessions up to `endDate` (inclusive), oldest first. */
  sessions(
    endDate: string,
    count: number,
    calendarId?: string,
  ): Promise<SessionWindow[]>;
  session(sessionDate: string, calendarId?: string): Promise<SessionWindow>;
  bars(request: BarsRequest): Promise<MarketBarsResponse>;
  /** US day-market (overnight) bars from Alpaca BOATS, a stream of their own (ADR 0030). */
  overnightBars(request: OvernightBarsRequest): Promise<MarketBarsResponse>;
  manualLedger(): Promise<ManualLedgerResponse>;
  scenarioLedger(): Promise<ScenarioLedgerResponse>;
  recordManual(request: ManualExecutionRequest): Promise<ManualExecution>;
  recordScenario(request: ScenarioRequest): Promise<ScenarioDefinition>;
  importedLedger(): Promise<ImportedLedgerResponse>;
  /** A computed comparison of the broker ledger with manual and imported executions. */
  /** `limit`: only the newest records; the summary counts all of them. */
  reconciliation(
    query?: ReconciliationQuery,
  ): Promise<TradeReconciliationResponse>;
  /** Executions reported by the broker API (one per order), kept as their own ledger. */
  /** One instrument's executions, or with `limit` only the newest ones; positions
   * always cover the whole ledger (or instrument). */
  brokerLedger(options?: {
    instrumentId?: string;
    limit?: number;
  }): Promise<BrokerLedgerResponse>;
  /** Holdings, buying power, commission rates and open orders of every Toss account. */
  brokerAccount(): Promise<BrokerAccountSnapshot>;
  sellableQuantity(
    accountSeq: number,
    symbol: string,
  ): Promise<BrokerSellableQuantity>;
  /** Store the executions of closed, filled orders in the broker ledger. */
  syncBroker(): Promise<BrokerSyncResult>;
  /** Store the Kiwoom account's filled US orders since a date (YYYY-MM-DD; default a year). */
  syncKiwoom(since?: string): Promise<BrokerSyncResult>;
  /** Kiwoom Korean stock trades (settlement lines, no fill times), at most a year. */
  syncKiwoomDomestic(since?: string): Promise<BrokerSyncResult>;
  /** Kiwoom US commission and tax per settlement line (positions add them). */
  syncKiwoomFees(since?: string): Promise<BrokerSyncResult>;
  previewImport(
    request: StatementImportRequest,
  ): Promise<ImportPreviewResponse>;
  commitImport(
    request: StatementImportRequest & {
      decisions: Record<string, "import" | "skip">;
    },
  ): Promise<ImportBatch>;
}

export type ImportMapping = ImportPreviewResponse["mapping"];

/** A broker statement CSV read in the browser; the API never stores the file itself. */
export interface StatementImportRequest {
  file_name: string;
  content: string;
  broker_label: string;
  timestamp_timezone: "America/New_York" | "UTC";
  delimiter: "," | ";" | "\t";
  mapping: ImportMapping | null;
}

/** What the user asserts about their own fill; the clicked candle is context only. */
export interface ManualExecutionRequest {
  execution_id: string;
  instrument_id: string;
  side: "buy" | "sell";
  quantity: string;
  price: string;
  fees: string;
  time_precision: "exact" | "minute" | "approximate" | "unknown";
  executed_date_et: string | null;
  executed_time_et: string | null;
  selected_interval_start_utc: string;
  selected_interval_end_utc: string;
  memo: string;
}

export interface ScenarioRequest {
  scenario_id: string;
  instrument_id: string;
  direction: "long" | "short";
  quantity: string;
  entry_price: string;
  exit_price: string;
  fee_assumption: string;
  slippage_assumption: string;
  entry_interval_start_utc: string;
  exit_date_et: string;
  exit_time_et: string;
}

export interface OvernightBarsRequest {
  instrumentId: string;
  timeframe: Timeframe;
  /** Trading days whose day-market sessions are wanted (at most 14). */
  fromDate: string;
  toDate: string;
  asOfUtc?: string;
}

export interface BarsRequest {
  instrumentId: string;
  timeframe: Timeframe;
  startUtc: string;
  endUtc: string;
  /** Point-in-time cutoff: only bars this account could have had by then. */
  asOfUtc?: string;
}

function hasKind<T extends { kind: string }>(
  value: unknown,
  kind: T["kind"],
): value is T {
  return (
    typeof value === "object" &&
    value !== null &&
    (value as { kind?: unknown }).kind === kind
  );
}

export interface ApiTarget {
  baseUrl: string;
  fetcher: typeof fetch;
  timeoutMs: number;
}

/**
 * One call to the local API. Non-2xx responses become a `MarketApiError` carrying the
 * error envelope; a 204 resolves to null.
 */
export async function callApi(
  { baseUrl, fetcher, timeoutMs }: ApiTarget,
  path: string,
  init: { method?: string; body?: unknown } = {},
): Promise<unknown> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetcher(`${baseUrl}${path}`, {
      signal: controller.signal,
      ...(init.method ? { method: init.method } : {}),
      ...(init.body === undefined
        ? {}
        : {
            method: init.method ?? "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(init.body),
          }),
    });
    if (response.status === 204) {
      return null;
    }
    const payload: unknown = await response.json().catch(() => null);
    if (!response.ok) {
      const error = (payload as { error?: Record<string, unknown> } | null)
        ?.error;
      throw new MarketApiError(
        typeof error?.message === "string"
          ? error.message
          : `HTTP ${response.status}`,
        typeof error?.code === "string" ? error.code : "http_error",
        typeof error?.request_id === "string" ? error.request_id : null,
      );
    }
    return payload;
  } catch (error) {
    if (error instanceof MarketApiError) {
      throw error;
    }
    throw new MarketApiError(
      error instanceof Error ? error.message : "Network error",
      "network_error",
    );
  } finally {
    window.clearTimeout(timeout);
  }
}

/** `callApi`, then a check that the response is the expected contract kind. */
export async function callForKind<T extends { kind: string }>(
  target: ApiTarget,
  path: string,
  kind: T["kind"],
  init: { method?: string; body?: unknown } = {},
): Promise<T> {
  const payload = await callApi(target, path, init);
  if (!hasKind<T>(payload, kind)) {
    throw new MarketApiError(
      `Unexpected response for ${kind}`,
      "invalid_response",
    );
  }
  return payload;
}

/**
 * Thin client for the local API. The API validates every response against the
 * shared contract before sending it; the client only checks the document kind, so
 * the desktop needs no runtime schema compiler (Tauri's CSP forbids `eval`).
 */
export function createMarketApi(
  baseUrl: string,
  fetcher: typeof fetch = fetch,
  timeoutMs = 10_000,
): MarketApi {
  const target = { baseUrl, fetcher, timeoutMs };
  function get<T extends { kind: string }>(
    path: string,
    kind: T["kind"],
    body?: unknown,
  ): Promise<T> {
    return callForKind<T>(target, path, kind, { body });
  }

  const session = (sessionDate: string, calendarId = "XNAS") =>
    get<SessionWindow>(
      `/v1/market/sessions/${encodeURIComponent(sessionDate)}?calendar_id=${calendarId}`,
      "session_window",
    );
  const bars = ({
    instrumentId,
    timeframe,
    startUtc,
    endUtc,
    asOfUtc,
  }: BarsRequest) => {
    const query = new URLSearchParams({
      instrument_id: instrumentId,
      timeframe,
      start_utc: startUtc,
      end_utc: endUtc,
      ...(asOfUtc ? { as_of_utc: asOfUtc } : {}),
    });
    return get<MarketBarsResponse>(
      `/v1/market/bars?${query.toString()}`,
      "market_bars_response",
    );
  };

  const overnightBars = ({
    instrumentId,
    timeframe,
    fromDate,
    toDate,
    asOfUtc,
  }: OvernightBarsRequest) => {
    const query = new URLSearchParams({
      instrument_id: instrumentId,
      timeframe,
      from_date: fromDate,
      to_date: toDate,
      ...(asOfUtc ? { as_of_utc: asOfUtc } : {}),
    });
    return get<MarketBarsResponse>(
      `/v1/market/overnight-bars?${query.toString()}`,
      "market_bars_response",
    );
  };
  return {
    overnightBars,
    async quote(instrumentId, sessionDate) {
      const window = await session(
        sessionDate,
        zoneOf(instrumentId).calendarId,
      );
      if (
        !window.is_trading_day ||
        !window.regular_open_utc ||
        !window.regular_close_utc
      ) {
        return null;
      }
      const response = await bars({
        instrumentId,
        timeframe: "60m",
        startUtc: window.regular_open_utc,
        endUtc: window.regular_close_utc,
      });
      const first = response.bars.at(0);
      const last = response.bars.at(-1);
      return first && last
        ? {
            instrumentId,
            sessionDate,
            open: first.open,
            last: last.close,
            lastFinal: last.is_final,
          }
        : null;
    },
    async instruments() {
      const response = await get<MarketInstrumentsResponse>(
        "/v1/market/instruments",
        "market_instruments_response",
      );
      return response.instruments;
    },
    research(dataset, key, params) {
      const query = new URLSearchParams(params);
      if (key) query.set("key", key);
      return get<ResearchTable>(
        `/v1/research/${encodeURIComponent(dataset)}?${query.toString()}`,
        "research_table",
      );
    },
    openRealtime(onEvent) {
      return openRealtime(
        `${baseUrl.replace(/^http/, "ws")}/v1/realtime`,
        onEvent,
      );
    },
    stockInfo(instrumentId) {
      const params = new URLSearchParams({ instrument_id: instrumentId });
      return get<StockInfo>(
        `/v1/market/stock-info?${params.toString()}`,
        "stock_info",
      );
    },
    exchangeRate() {
      return get<ExchangeRate>("/v1/market/exchange-rate", "exchange_rate");
    },
    brokerCalendar(market) {
      return get<BrokerMarketCalendar>(
        `/v1/market/broker-calendar?market=${market}`,
        "broker_market_calendar",
      );
    },
    liveQuote(instrumentId) {
      const params = new URLSearchParams({ instrument_id: instrumentId });
      return get<MarketQuote>(
        `/v1/market/quote?${params.toString()}`,
        "market_quote",
      );
    },
    searchSymbols(query, limit = 20) {
      const params = new URLSearchParams({ q: query, limit: String(limit) });
      return get<SymbolSearchResponse>(
        `/v1/market/symbols?${params.toString()}`,
        "symbol_search_response",
      );
    },
    session,
    bars,
    async sessions(endDate, count, calendarId = "XNAS") {
      const query = new URLSearchParams({
        end_date: endDate,
        count: String(count),
        calendar_id: calendarId,
      });
      const response = await get<SessionListResponse>(
        `/v1/market/sessions?${query.toString()}`,
        "session_list_response",
      );
      return response.sessions;
    },
    manualLedger() {
      return get<ManualLedgerResponse>(
        "/v1/trades/manual",
        "manual_ledger_response",
      );
    },
    scenarioLedger() {
      return get<ScenarioLedgerResponse>(
        "/v1/scenarios",
        "scenario_ledger_response",
      );
    },
    recordManual(request) {
      return get<ManualExecution>(
        "/v1/trades/manual",
        "manual_execution",
        request,
      );
    },
    recordScenario(request) {
      return get<ScenarioDefinition>(
        "/v1/scenarios",
        "scenario_definition",
        request,
      );
    },
    brokerLedger(options = {}) {
      const query = new URLSearchParams({
        ...(options.instrumentId
          ? { instrument_id: options.instrumentId }
          : {}),
        ...(options.limit ? { limit: String(options.limit) } : {}),
      }).toString();
      return get<BrokerLedgerResponse>(
        `/v1/trades/broker${query ? `?${query}` : ""}`,
        "broker_ledger_response",
      );
    },
    brokerAccount() {
      return get<BrokerAccountSnapshot>(
        "/v1/brokers/toss/account",
        "broker_account_snapshot",
      );
    },
    sellableQuantity(accountSeq, symbol) {
      const params = new URLSearchParams({
        account_seq: String(accountSeq),
        symbol,
      });
      return get<BrokerSellableQuantity>(
        `/v1/brokers/toss/sellable?${params.toString()}`,
        "broker_sellable_quantity",
      );
    },
    syncBroker() {
      // Paging through a long order history can take a while.
      return callForKind<BrokerSyncResult>(
        { ...target, timeoutMs: 120_000 },
        "/v1/brokers/toss/sync",
        "broker_sync_result",
        { method: "POST" },
      );
    },
    syncKiwoom(since) {
      const query = since
        ? `?${new URLSearchParams({ since }).toString()}`
        : "";
      // One request per order date with fills: a year can take a few minutes.
      return callForKind<BrokerSyncResult>(
        { ...target, timeoutMs: 600_000 },
        `/v1/brokers/kiwoom/sync${query}`,
        "broker_sync_result",
        { method: "POST" },
      );
    },
    syncKiwoomFees(since) {
      const query = since
        ? `?${new URLSearchParams({ since }).toString()}`
        : "";
      return callForKind<BrokerSyncResult>(
        { ...target, timeoutMs: 1_800_000 },
        `/v1/brokers/kiwoom/sync-fees${query}`,
        "broker_sync_result",
        { method: "POST" },
      );
    },
    syncKiwoomDomestic(since) {
      const query = since
        ? `?${new URLSearchParams({ since }).toString()}`
        : "";
      return callForKind<BrokerSyncResult>(
        { ...target, timeoutMs: 600_000 },
        `/v1/brokers/kiwoom/sync-domestic${query}`,
        "broker_sync_result",
        { method: "POST" },
      );
    },
    importedLedger() {
      return get<ImportedLedgerResponse>(
        "/v1/trades/imported",
        "imported_ledger_response",
      );
    },
    reconciliation(options = {}) {
      const query = new URLSearchParams({
        ...(options.instrumentId
          ? { instrument_id: options.instrumentId }
          : {}),
        ...(options.fromDate ? { from_date: options.fromDate } : {}),
        ...(options.toDate ? { to_date: options.toDate } : {}),
        ...(options.status ? { status: options.status } : {}),
        ...(options.limit ? { limit: String(options.limit) } : {}),
      }).toString();
      return get<TradeReconciliationResponse>(
        `/v1/trades/reconciliation${query ? `?${query}` : ""}`,
        "trade_reconciliation_response",
      );
    },
    previewImport(request) {
      return get<ImportPreviewResponse>(
        "/v1/imports/trades/preview",
        "import_preview_response",
        request,
      );
    },
    commitImport(request) {
      return get<ImportBatch>(
        "/v1/imports/trades/commit",
        "import_batch",
        request,
      );
    },
  };
}
