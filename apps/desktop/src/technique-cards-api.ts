import { callForKind, type ApiTarget } from "./market-api";

/** Technique sources and cards (ADR 0056 L1·L2). App-local documents, not in contracts. */

export interface TechniqueSource {
  item_ref: string;
  title: string;
  url: string;
  source_key: string;
  tags: string[];
  delivered_at: string;
  eligibility: string[];
  card_ids: string[];
}

export interface TechniquePreview {
  kind: "technique_source_preview";
  item_ref: string;
  title: string;
  url: string;
  lines: number;
  parts: number;
  characters: number;
  model: string;
  fallback: string;
  models: string[];
  estimated_cost_usd: string;
  estimate_note: string;
  verification_status: string;
  data_grade: string;
  recheck_outcome: string;
}

export interface TechniqueJob {
  kind: "technique_card_job";
  job_id: string;
  state?: "running" | "done" | "failed";
  error?: string;
  card_id?: string | null;
}

export interface Operand {
  kind: string;
  name: string;
  period: number | null;
  value: number | null;
}

export interface Citation {
  locator: string;
  quote: string;
}

export interface CardCondition {
  field: "entry" | "exit" | "stop_loss";
  side: string;
  text: string;
  basis: string;
  left: Operand;
  comparator: string;
  right: Operand;
  timeframe: string;
  citations: Citation[];
  value_citations: { value: number; locator: string; quote: string }[];
}

export interface Statement {
  text: string;
  basis: string;
  citations: Citation[];
}

export interface Ambiguity {
  text: string;
  question: string;
  citations: Citation[];
}

export interface StructuredCard {
  source_id: string;
  title: string;
  conditions: CardCondition[];
  timeframe: Statement[];
  universe: Statement[];
  position: Statement[];
  inexpressible: Statement[];
  ambiguities: Ambiguity[];
}

export interface Flag {
  code: "unbacked" | "no_criterion" | "inference";
  reason: string;
}

export interface Resolution {
  action: "confirm" | "edit" | "drop";
  condition?: CardCondition | null;
  note?: string;
}

export interface CardUsage {
  input_tokens: number;
  output_tokens: number;
  cost_usd: string;
  models: string[];
  calls: number;
  parts: number;
  flags: Record<string, number>;
}

export interface CardSummary {
  card_id: string;
  item_ref: string;
  title: string;
  url: string;
  version: number;
  usage: CardUsage;
  recheck_outcome: string;
  rechecked_at: string;
  withdrawn: boolean;
  ready: boolean;
  blocks: string[];
  corrected: number;
  created_at: string;
  updated_at: string;
  flags?: Record<string, number>;
}

export interface TimelineRow {
  time: string;
  text: string;
  conditions: number[];
  universe: number[];
}

export interface TechniqueCard extends CardSummary {
  kind: "technique_card";
  card: StructuredCard;
  checks: {
    conditions: { index: number; field: string; flags: Flag[] }[];
    counts: Record<string, number>;
  };
  resolutions: {
    conditions: Record<string, Resolution>;
    ambiguities: Record<string, string>;
  };
  verification_status: string;
  timeline: TimelineRow[] | null;
}

export interface TechniqueCardsApi {
  sources(refresh?: boolean): Promise<TechniqueSource[]>;
  /** `model`: the assigned sonnet when left out, or another choosable model. */
  preview(itemRef: string, model?: string): Promise<TechniquePreview>;
  start(itemRef: string, model?: string): Promise<TechniqueJob>;
  job(jobId: string): Promise<TechniqueJob>;
  cards(): Promise<CardSummary[]>;
  card(cardId: string): Promise<TechniqueCard>;
  resolve(
    cardId: string,
    version: number,
    conditions: Record<string, Resolution>,
    ambiguities: Record<string, string>,
  ): Promise<CardSummary>;
  recheck(cardId: string): Promise<CardSummary>;
}

export function createTechniqueCardsApi(
  baseUrl: string,
  fetcher: typeof fetch = fetch,
  // Listing reads every item through the library CLI (~1 s each).
  timeoutMs = 60_000,
): TechniqueCardsApi {
  const target: ApiTarget = { baseUrl, fetcher, timeoutMs };
  return {
    sources: async (refresh = false) =>
      (
        await callForKind<{
          kind: "technique_sources";
          sources: TechniqueSource[];
        }>(
          target,
          `/v1/technique-sources${refresh ? "?refresh=true" : ""}`,
          "technique_sources",
        )
      ).sources,
    preview: (itemRef, model) =>
      callForKind<TechniquePreview>(
        target,
        "/v1/technique-sources/preview",
        "technique_source_preview",
        { body: { item_ref: itemRef, ...(model ? { model } : {}) } },
      ),
    start: (itemRef, model) =>
      callForKind<TechniqueJob>(
        target,
        "/v1/technique-cards",
        "technique_card_job",
        {
          body: { item_ref: itemRef, ...(model ? { model } : {}) },
        },
      ),
    job: (jobId) =>
      callForKind<TechniqueJob>(
        target,
        `/v1/technique-cards/jobs/${encodeURIComponent(jobId)}`,
        "technique_card_job",
      ),
    cards: async () =>
      (
        await callForKind<{ kind: "technique_cards"; cards: CardSummary[] }>(
          target,
          "/v1/technique-cards",
          "technique_cards",
        )
      ).cards,
    card: (cardId) =>
      callForKind<TechniqueCard>(
        target,
        `/v1/technique-cards/${encodeURIComponent(cardId)}`,
        "technique_card",
      ),
    resolve: (cardId, version, conditions, ambiguities) =>
      callForKind<CardSummary & { kind: "technique_card" }>(
        target,
        `/v1/technique-cards/${encodeURIComponent(cardId)}/resolutions`,
        "technique_card",
        { body: { version, conditions, ambiguities } },
      ),
    recheck: (cardId) =>
      callForKind<CardSummary & { kind: "technique_card" }>(
        target,
        `/v1/technique-cards/${encodeURIComponent(cardId)}/recheck`,
        "technique_card",
        { method: "POST" },
      ),
  };
}

const SIDE_TEXT: Record<string, string> = {
  cross_above: "↗ crosses above",
  cross_below: "↘ crosses below",
  above: ">",
  below: "<",
  at_or_above: "≥",
  at_or_below: "≤",
  equal: "=",
  touch: "touches",
  rises: "rises",
  falls: "falls",
  none: "—",
  other: "?",
};

/** One operand as a short label: `ema(50)`, `value 30`, `2 atr_multiple`. */
export function operandLabel(operand: Operand): string {
  if (operand.kind === "none") return "—";
  const name =
    operand.name === "none" || operand.name === "other" ? "" : operand.name;
  const period = operand.period === null ? "" : `(${operand.period})`;
  if (operand.kind === "indicator" || operand.kind === "price") {
    return `${name || operand.kind}${period}`;
  }
  const value = operand.value === null ? "?" : String(operand.value);
  const unit =
    operand.kind === "percent" ? "%" : ` ${operand.kind.replace("_", " ")}`;
  return `${value}${unit}${name ? ` ${name}${period}` : ""}`;
}

export function slotsLabel(condition: CardCondition): string {
  return `${operandLabel(condition.left)} ${SIDE_TEXT[condition.comparator] ?? condition.comparator} ${operandLabel(condition.right)}`;
}
