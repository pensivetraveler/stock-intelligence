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

function decimal(document: Document, key: string): number {
  return Number(asString(document, key));
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

export function semanticErrors(document: Document): string[] {
  const errors: string[] = [];

  if (document.kind === "price_bar") {
    const start = Date.parse(asString(document, "interval_start_utc"));
    const end = Date.parse(asString(document, "interval_end_utc"));
    if (end <= start) {
      errors.push("interval_end_utc must be after interval_start_utc");
    }
    const open = decimal(document, "open");
    const high = decimal(document, "high");
    const low = decimal(document, "low");
    const close = decimal(document, "close");
    if (high < Math.max(open, close, low)) {
      errors.push("high must be greater than or equal to open, close, and low");
    }
    if (low > Math.min(open, close, high)) {
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
      lineage.kind === "aggregated_1m" &&
      (document.timeframe === "1m" || lineage.source_dataset_revision === null)
    ) {
      errors.push(
        "aggregated lineage requires a derived timeframe and source revision",
      );
    }
  }

  if (document.kind === "manual_execution") {
    const start = Date.parse(asString(document, "selected_interval_start_utc"));
    const end = Date.parse(asString(document, "selected_interval_end_utc"));
    if (end <= start) {
      errors.push("selected interval end must be after start");
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
