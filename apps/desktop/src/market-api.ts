import type {
  ImportBatch,
  ImportedLedgerResponse,
  ImportPreviewResponse,
  ManualExecution,
  ManualLedgerResponse,
  MarketBarsResponse,
  MarketInstrumentsResponse,
  ScenarioDefinition,
  ScenarioLedgerResponse,
  SessionWindow,
} from "@siw/contracts/types";

export type Timeframe = "1m" | "5m" | "15m" | "30m" | "60m";
export const TIMEFRAMES: readonly Timeframe[] = [
  "1m",
  "5m",
  "15m",
  "30m",
  "60m",
];
export type InstrumentListing =
  MarketInstrumentsResponse["instruments"][number];

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

export interface MarketApi {
  instruments(): Promise<InstrumentListing[]>;
  session(sessionDate: string): Promise<SessionWindow>;
  bars(request: BarsRequest): Promise<MarketBarsResponse>;
  manualLedger(): Promise<ManualLedgerResponse>;
  scenarioLedger(): Promise<ScenarioLedgerResponse>;
  recordManual(request: ManualExecutionRequest): Promise<ManualExecution>;
  recordScenario(request: ScenarioRequest): Promise<ScenarioDefinition>;
  importedLedger(): Promise<ImportedLedgerResponse>;
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

export interface BarsRequest {
  instrumentId: string;
  timeframe: Timeframe;
  startUtc: string;
  endUtc: string;
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
  async function get<T extends { kind: string }>(
    path: string,
    kind: T["kind"],
    body?: unknown,
  ): Promise<T> {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetcher(`${baseUrl}${path}`, {
        signal: controller.signal,
        ...(body === undefined
          ? {}
          : {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(body),
            }),
      });
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
      if (!hasKind<T>(payload, kind)) {
        throw new MarketApiError(
          `Unexpected response for ${kind}`,
          "invalid_response",
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

  return {
    async instruments() {
      const response = await get<MarketInstrumentsResponse>(
        "/v1/market/instruments",
        "market_instruments_response",
      );
      return response.instruments;
    },
    session(sessionDate) {
      return get<SessionWindow>(
        `/v1/market/sessions/${encodeURIComponent(sessionDate)}`,
        "session_window",
      );
    },
    bars({ instrumentId, timeframe, startUtc, endUtc }) {
      const query = new URLSearchParams({
        instrument_id: instrumentId,
        timeframe,
        start_utc: startUtc,
        end_utc: endUtc,
      });
      return get<MarketBarsResponse>(
        `/v1/market/bars?${query.toString()}`,
        "market_bars_response",
      );
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
    importedLedger() {
      return get<ImportedLedgerResponse>(
        "/v1/trades/imported",
        "imported_ledger_response",
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
