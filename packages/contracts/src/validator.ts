import Ajv2020, { type ErrorObject } from "ajv/dist/2020.js";
import addFormats from "ajv-formats";

import schema from "../schemas/contracts.schema.json";

const ajv = new Ajv2020({ allErrors: true, strict: true });
addFormats(ajv);
const validateSchema = ajv.compile(schema);

type Document = Record<string, unknown>;

function asString(document: Document, key: string): string {
  const value = document[key];
  if (typeof value !== "string") {
    throw new TypeError(`${key} must be a string after schema validation`);
  }
  return value;
}

interface ParsedDecimal {
  negative: boolean;
  integer: string;
  fraction: string;
}

// Exact comparison of schema-validated decimal strings. `Number` would round values
// such as "250.10000000000000001" and disagree with the Python `Decimal` validator.
function parseDecimal(value: string): ParsedDecimal {
  const negative = value.startsWith("-");
  const [integerPart, fractionPart = ""] = (
    negative ? value.slice(1) : value
  ).split(".");
  const integer = integerPart === "0" ? "" : integerPart;
  const fraction = fractionPart.replace(/0+$/, "");
  return {
    negative: negative && (integer !== "" || fraction !== ""),
    integer,
    fraction,
  };
}

function compareMagnitude(left: ParsedDecimal, right: ParsedDecimal): number {
  if (left.integer.length !== right.integer.length) {
    return left.integer.length < right.integer.length ? -1 : 1;
  }
  if (left.integer !== right.integer) {
    return left.integer < right.integer ? -1 : 1;
  }
  const width = Math.max(left.fraction.length, right.fraction.length);
  const leftFraction = left.fraction.padEnd(width, "0");
  const rightFraction = right.fraction.padEnd(width, "0");
  if (leftFraction === rightFraction) {
    return 0;
  }
  return leftFraction < rightFraction ? -1 : 1;
}

/** Kinds whose currency must match the instrument's market (kr: listings trade in KRW). */
const CURRENCY_BY_MARKET_KINDS = new Set([
  "manual_execution",
  "scenario_definition",
  "imported_execution",
  "broker_order_execution",
  "broker_confirmed_execution",
  "market_quote",
  "paper_order_intent",
  "paper_execution",
]);

export function compareDecimal(left: string, right: string): number {
  const a = parseDecimal(left);
  const b = parseDecimal(right);
  if (a.negative !== b.negative) {
    return a.negative ? -1 : 1;
  }
  const magnitude = compareMagnitude(a, b);
  return a.negative ? -magnitude : magnitude;
}

function pointInTimeErrors(bars: Document[], coverage: Document): string[] {
  const errors: string[] = [];
  const requestedStart = Date.parse(asString(coverage, "requested_start_utc"));
  const requestedEnd = Date.parse(asString(coverage, "requested_end_utc"));
  const cutoff =
    coverage.point_in_time_cutoff_utc === null
      ? null
      : Date.parse(asString(coverage, "point_in_time_cutoff_utc"));
  let previousStart: number | null = null;
  for (const bar of bars) {
    const start = Date.parse(asString(bar, "interval_start_utc"));
    const end = Date.parse(asString(bar, "interval_end_utc"));
    if (start < requestedStart || end > requestedEnd) {
      errors.push("response bar must lie within the requested coverage range");
    }
    if (previousStart !== null && start <= previousStart) {
      errors.push(
        "response bars must be strictly ordered by interval_start_utc",
      );
    }
    previousStart = start;
    if (
      cutoff !== null &&
      Date.parse(asString(bar, "available_at_utc")) > cutoff
    ) {
      errors.push("response bar was not available at the point-in-time cutoff");
    }
  }
  return errors;
}

function paperIntentErrors(document: Document): string[] {
  const errors: string[] = [];
  const data = document.data as Document;
  const barStart = Date.parse(asString(data, "signal_bar_start_utc"));
  const barEnd = Date.parse(asString(data, "signal_bar_end_utc"));
  if (barEnd <= barStart) {
    errors.push("signal bar end must be after its start");
  }
  if (Date.parse(asString(data, "data_as_of_utc")) < barEnd) {
    errors.push(
      "a signal cannot use a bar that had not ended at the data cutoff",
    );
  }
  if (
    Date.parse(asString(document, "created_at_utc")) <
    Date.parse(asString(data, "data_as_of_utc"))
  ) {
    errors.push("an intent cannot be created before its data cutoff");
  }
  const order = document.order as Document;
  if (compareDecimal(asString(order, "quantity"), "0") <= 0) {
    errors.push("intent quantity must be positive");
  }
  if (
    order.order_type === "limit" &&
    (order.limit_price === null ||
      compareDecimal(asString(order, "limit_price"), "0") <= 0 ||
      order.estimated_max_price !== null ||
      order.market_buffer_bps !== null)
  ) {
    errors.push(
      "a limit order has a positive limit price and no market estimate",
    );
  }
  if (
    order.order_type === "market" &&
    (order.limit_price !== null ||
      order.estimated_max_price === null ||
      order.market_buffer_bps === null ||
      compareDecimal(asString(order, "estimated_max_price"), "0") <= 0)
  ) {
    errors.push(
      "a market order has an estimated maximum price and no limit price",
    );
  }
  const check = document.limit_check as Document;
  const reasons = check.reasons as unknown[];
  const limits = check.limits as Document;
  if (check.passed === reasons.length > 0) {
    errors.push("a limit check lists reasons exactly when it fails");
  }
  if (check.passed === true && limits.max_order_amount_krw !== null) {
    if (check.order_amount_krw === null) {
      errors.push("a passed amount limit must record the order amount in KRW");
    }
    if (document.currency === "USD" && document.fx === null) {
      errors.push(
        "a USD order checked against a KRW limit must record its exchange rate",
      );
    }
  }
  if (
    check.passed === true &&
    !(limits.allowed_instrument_ids as unknown[]).includes(
      document.instrument_id,
    )
  ) {
    errors.push("a passed intent must be for an allowed instrument");
  }
  return errors;
}

export function semanticErrors(document: Document): string[] {
  const errors: string[] = [];

  if (document.kind === "price_bar") {
    const start = Date.parse(asString(document, "interval_start_utc"));
    const end = Date.parse(asString(document, "interval_end_utc"));
    if (end <= start) {
      errors.push("interval_end_utc must be after interval_start_utc");
    }
    const open = asString(document, "open");
    const high = asString(document, "high");
    const low = asString(document, "low");
    const close = asString(document, "close");
    if ([open, close, low].some((price) => compareDecimal(high, price) < 0)) {
      errors.push("high must be greater than or equal to open, close, and low");
    }
    if ([open, close, high].some((price) => compareDecimal(low, price) > 0)) {
      errors.push("low must be less than or equal to open, close, and high");
    }
    const lineage = document.lineage as Document;
    const expected = lineage.expected_source_bar_count as number;
    const observed = lineage.observed_source_bar_count as number;
    if (observed > expected) {
      errors.push(
        "observed source bar count cannot exceed expected source bar count",
      );
    }
    if (
      document.quality === "complete" &&
      (observed !== expected || document.is_final !== true)
    ) {
      errors.push(
        "complete bar quality requires all source bars and finalization",
      );
    }
    if (document.quality === "provisional" && document.is_final === true) {
      errors.push("provisional bar quality cannot be final");
    }
    if (
      document.is_final === true &&
      Date.parse(asString(document, "available_at_utc")) < end
    ) {
      errors.push("a final bar cannot be available before its interval ends");
    }
    if (
      lineage.kind === "provider" &&
      (expected !== 1 ||
        observed !== 1 ||
        lineage.source_dataset_revision !== null)
    ) {
      errors.push("provider lineage must represent one direct source bar");
    }
    if (
      (lineage.kind === "aggregated_1m" || lineage.kind === "aggregated_1d") &&
      (document.timeframe === lineage.source_timeframe ||
        lineage.source_dataset_revision === null)
    ) {
      errors.push(
        "aggregated lineage requires a derived timeframe and source revision",
      );
    }
    const expectedSource =
      lineage.kind === "aggregated_1m"
        ? "1m"
        : lineage.kind === "aggregated_1d"
          ? "1d"
          : null;
    if (
      expectedSource !== null &&
      lineage.source_timeframe !== expectedSource
    ) {
      errors.push(
        `${String(lineage.kind)} lineage must come from ${expectedSource} bars`,
      );
    }
    if (
      lineage.kind === "provider" &&
      lineage.source_timeframe !== document.timeframe
    ) {
      errors.push(
        "provider lineage must name the bar's own timeframe as its source",
      );
    }
  }

  if (document.kind === "manual_execution") {
    const start = Date.parse(asString(document, "selected_interval_start_utc"));
    const end = Date.parse(asString(document, "selected_interval_end_utc"));
    if (end <= start) {
      errors.push("selected interval end must be after start");
    }
    if (
      compareDecimal(asString(document, "quantity"), "0") <= 0 ||
      compareDecimal(asString(document, "price"), "0") <= 0
    ) {
      errors.push("manual execution quantity and price must be positive");
    }
    const executedAt = document.executed_at_utc;
    if ((executedAt === null) !== (document.time_precision === "unknown")) {
      errors.push(
        "executed_at_utc is null exactly when time_precision is unknown",
      );
    }
    if (
      typeof executedAt === "string" &&
      Date.parse(executedAt) > Date.parse(asString(document, "asserted_at_utc"))
    ) {
      errors.push(
        "an execution cannot be claimed for a time after it was recorded",
      );
    }
  }

  if (document.kind === "scenario_definition") {
    if (compareDecimal(asString(document, "quantity"), "0") <= 0) {
      errors.push("scenario quantity must be positive");
    }
    if (
      compareDecimal(asString(document, "entry_price"), "0") <= 0 ||
      compareDecimal(asString(document, "exit_price"), "0") <= 0
    ) {
      errors.push("scenario entry and exit prices must be positive");
    }
    if (
      Date.parse(asString(document, "exit_interval_start_utc")) <=
      Date.parse(asString(document, "entry_interval_start_utc"))
    ) {
      errors.push("scenario exit must come after its entry");
    }
  }

  if (document.kind === "imported_execution") {
    if (
      compareDecimal(asString(document, "quantity"), "0") <= 0 ||
      compareDecimal(asString(document, "price"), "0") <= 0
    ) {
      errors.push("imported execution quantity and price must be positive");
    }
    const review = document.duplicate_review as Document;
    if (
      review.decision === "imported_after_review" &&
      (review.related as unknown[]).length === 0
    ) {
      errors.push("a reviewed duplicate must name the records it resembles");
    }
  }

  if (
    document.kind === "import_preview_response" ||
    document.kind === "imported_ledger_response"
  ) {
    const rows = document.rows as Document[] | undefined;
    const executions = rows
      ? rows
          .filter((row) => row.execution !== null)
          .map((row) => row.execution as Document)
      : (document.executions as Document[]);
    for (const execution of executions) {
      errors.push(...semanticErrors(execution));
    }
    if (rows) {
      const counts: Record<string, number> = {};
      for (const key of Object.keys(document.summary as Document)) {
        counts[key] = 0;
      }
      for (const row of rows) {
        if ((row.status === "invalid") !== (row.execution === null)) {
          errors.push("only invalid preview rows lack an execution");
        }
        if (
          row.status === "invalid" &&
          (row.errors as unknown[]).length === 0
        ) {
          errors.push("an invalid preview row must say why");
        }
        counts[row.status as string] = (counts[row.status as string] ?? 0) + 1;
      }
      const summary = document.summary as Record<string, number>;
      if (Object.keys(counts).some((key) => counts[key] !== summary[key])) {
        errors.push("preview summary must count the rows by status");
      }
    }
  }

  if (document.kind === "manual_ledger_response") {
    for (const execution of document.executions as Document[]) {
      errors.push(...semanticErrors(execution));
    }
  }

  if (document.kind === "scenario_ledger_response") {
    for (const item of document.scenarios as Document[]) {
      errors.push(...semanticErrors(item.scenario as Document));
    }
  }

  if (document.kind === "session_window") {
    const boundaries = [
      "pre_open_utc",
      "regular_open_utc",
      "regular_close_utc",
      "post_close_utc",
    ].map((key) => document[key]);
    if (document.is_trading_day !== true) {
      if (
        boundaries.some((value) => value !== null) ||
        document.is_early_close === true
      ) {
        errors.push(
          "a non-trading day has no session boundaries or early close",
        );
      }
    } else if (boundaries.some((value) => value === null)) {
      errors.push("a trading day requires every session boundary");
    } else {
      const [preOpen, regularOpen, regularClose, postClose] = boundaries.map(
        (value) => Date.parse(value as string),
      );
      if (!(
        preOpen <= regularOpen &&
        regularOpen < regularClose &&
        regularClose <= postClose
      )) {
        errors.push(
          "session boundaries must be ordered pre <= open < close <= post",
        );
      }
    }
  }

  if (document.kind === "market_instruments_response") {
    const identifiers = (document.instruments as Document[]).map(
      (item) => (item.instrument as Document).instrument_id,
    );
    if (new Set(identifiers).size !== identifiers.length) {
      errors.push("instrument_id must be unique within the response");
    }
  }

  if (CURRENCY_BY_MARKET_KINDS.has(String(document.kind))) {
    const instrument = document.instrument_id;
    const currency = document.currency;
    if (
      typeof instrument === "string" &&
      (currency === "USD" || currency === "KRW")
    ) {
      const expected = instrument.startsWith("kr:") ? "KRW" : "USD";
      if (currency !== expected) {
        errors.push(`${instrument} trades in ${expected}`);
      }
    }
  }

  if (document.kind === "research_table") {
    const columns = document.columns as string[];
    if (new Set(columns).size !== columns.length) {
      errors.push("research_table columns must be unique");
    }
    if (
      (document.rows as unknown[][]).some(
        (row) => row.length !== columns.length,
      )
    ) {
      errors.push("every research_table row must have one cell per column");
    }
  }

  if (document.kind === "symbol_search_response") {
    const identifiers = (document.results as Document[]).map(
      (item) =>
        ((item.listing as Document).instrument as Document).instrument_id,
    );
    if (new Set(identifiers).size !== identifiers.length) {
      errors.push("instrument_id must be unique within the response");
    }
  }

  if (document.kind === "backtest_run_config") {
    const start = Date.parse(asString(document, "start_utc"));
    const end = Date.parse(asString(document, "end_utc"));
    if (end <= start) {
      errors.push("backtest end_utc must be after start_utc");
    }
  }

  if (document.kind === "coverage_manifest") {
    const start = Date.parse(asString(document, "requested_start_utc"));
    const end = Date.parse(asString(document, "requested_end_utc"));
    if (end <= start) {
      errors.push(
        "coverage requested_end_utc must be after requested_start_utc",
      );
    }
    const observed = document.observed_interval_count as number;
    const expected = document.expected_interval_count as number;
    const missing = document.missing_intervals as unknown[];
    if (observed + missing.length !== expected) {
      errors.push(
        "coverage observed plus missing intervals must equal expected intervals",
      );
    }
    if (
      document.coverage_status === "complete" &&
      (observed !== expected || missing.length > 0)
    ) {
      errors.push("complete coverage must contain every expected interval");
    }
    if (
      document.coverage_status === "partial" &&
      !(observed > 0 && observed < expected)
    ) {
      errors.push(
        "partial coverage must contain some but not all expected intervals",
      );
    }
    if (document.coverage_status === "empty" && observed !== 0) {
      errors.push("empty coverage must not contain observed intervals");
    }
  }

  if (document.kind === "paper_order_intent") {
    errors.push(...paperIntentErrors(document));
  }

  if (document.kind === "paper_order_submission") {
    const responded = document.responded_at_utc;
    if (
      typeof responded === "string" &&
      Date.parse(responded) < Date.parse(asString(document, "requested_at_utc"))
    ) {
      errors.push("a submission cannot be answered before it was requested");
    }
    if (document.status === "pending_response" && responded !== null) {
      errors.push("a pending submission has no response time");
    }
    if (
      (document.status === "accepted" || document.status === "rejected") &&
      responded === null
    ) {
      errors.push(
        "an accepted or rejected submission records when it was answered",
      );
    }
    if (document.status === "accepted" && document.broker_order_ref === null) {
      errors.push(
        "an accepted submission must carry the broker's order reference",
      );
    }
  }

  if (document.kind === "paper_execution") {
    if (
      compareDecimal(asString(document, "quantity"), "0") <= 0 ||
      compareDecimal(asString(document, "price"), "0") <= 0
    ) {
      errors.push("paper execution quantity and price must be positive");
    }
    if (
      (document.executed_at_utc === null) !==
      (document.time_meaning === "time_unresolved")
    ) {
      errors.push(
        "executed_at_utc is null exactly when its time is unresolved",
      );
    }
  }

  if (document.kind === "paper_runner_status") {
    if (
      document.armed === true &&
      (document.halted === true || document.approval_id === null)
    ) {
      errors.push("an armed runner must be unhalted and name its approval");
    }
    if ((document.halted === true) !== (document.halted_reason !== null)) {
      errors.push("halted_reason is set exactly while the runner is halted");
    }
  }

  if (document.kind === "market_bars_response") {
    const bars = document.bars as Document[];
    const coverage = document.coverage as Document;
    errors.push(...semanticErrors(coverage));
    for (const bar of bars) {
      errors.push(...semanticErrors(bar));
      for (const key of [
        "instrument_id",
        "timeframe",
        "source_id",
        "source_feed_scope",
        "adjustment",
        "dataset_revision",
      ]) {
        if (bar[key] !== coverage[key]) {
          errors.push(`response bar ${key} must match coverage`);
        }
      }
    }
    if (bars.length !== coverage.observed_interval_count) {
      errors.push(
        "response bar count must match observed coverage interval count",
      );
    }
    errors.push(...pointInTimeErrors(bars, coverage));
  }

  return errors;
}

function schemaErrors(): string[] {
  return (validateSchema.errors ?? []).map((error: ErrorObject) => {
    return `${error.instancePath || "/"} ${error.message ?? "is invalid"}`;
  });
}

export function validationErrors(document: unknown): string[] {
  if (!validateSchema(document)) {
    return schemaErrors();
  }
  return semanticErrors(document as Document);
}

export function isValidContract(document: unknown): boolean {
  return validationErrors(document).length === 0;
}
