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
