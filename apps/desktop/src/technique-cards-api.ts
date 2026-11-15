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
  default_model: string;
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
    /** Conditions the user wrote; missing on cards saved before this field. */
    added?: AddedCondition[];
  };
  verification_status: string;
  timeline: TimelineRow[] | null;
}

/** A condition the user adds, e.g. when an answer makes a rule the source left out. */
export interface AddedCondition {
  field: "entry" | "exit";
  text: string;
}

/** Models the user may pick for one extraction or draft. */
export const CHOOSABLE_MODELS = [
  "anthropic/claude-fable-5.2",
  "google/gemini-4-argon",
  "openai/gpt-6-astra",
  "claude-sonnet-5-5",
  "openai/gpt-6-luna",
] as const;

/** Short names for the screen. */
export const MODEL_NAMES: Record<string, string> = {
  "anthropic/claude-fable-5.2": "Fable 5.2",
  "google/gemini-4-argon": "Gemini 4 Argon",
  "openai/gpt-6-astra": "GPT-6 Astra",
  "claude-sonnet-5-5": "Sonnet 5.5",
  "openai/gpt-6-luna": "GPT-6 Luna",
};

export interface TechniqueSettings {
  kind: "technique_settings";
  default_model: string;
  /** The models the default may be: Fable, Argon, Astra. */
  default_choices: string[];
  /** Set when a saved default was no longer offered and was put back. */
  default_reset?: { from: string; to: string; at: string } | null;
  models: string[];
}

export interface DraftMapping {
  index: number;
  condition: string;
  status: "mapped" | "unconvertible" | "absent";
  rule_paths: string[];
  reason: string;
}

/** A reading the script took that the source did not state; saving needs the user's tick. */
export interface DraftAssumption {
  index: number;
  kind: "default_length" | "ma_as_sma" | "long_only";
  indicator?: string;
  param?: string;
  value?: number;
}

/** Converts by code alone: no model, no cost (script first, 2026-11-15). */
export const SCRIPT_MODEL = "script";

export interface TechniqueDraft {
  kind: "technique_card_draft";
  draft_id: string;
  card_id: string;
  state: "running" | "done" | "failed";
  model: string;
  error: string;
  result: {
    items: string[];
    conversion: {
      definition: Record<string, unknown> | null;
      mapping: DraftMapping[];
      problems: string[];
      notes: string;
      complete: boolean;
    };
    /** Item numbers the script converted; a model may not change them. */
    scripted?: number[];
    assumptions?: DraftAssumption[];
    unconvertible: number;
    blocking: string[];
    cost_usd: string;
  } | null;
  strategy_id: string | null;
  strategy_version: number | null;
  save_blocks: string[];
  needs_accept: boolean;
  needs_confirm?: boolean;
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
    added?: AddedCondition[],
  ): Promise<CardSummary>;
  recheck(cardId: string): Promise<CardSummary>;
  settings(): Promise<TechniqueSettings>;
  setDefaultModel(model: string): Promise<TechniqueSettings>;
  /** Rules strategy draft from a ready card (ADR 0056 L3). */
  startDraft(cardId: string, model: string): Promise<{ draft_id: string }>;
  draft(draftId: string): Promise<TechniqueDraft>;
  drafts(cardId: string): Promise<TechniqueDraft[]>;
  saveDraft(
    draftId: string,
    name: string,
    acceptUnconvertible: boolean,
    confirmAssumptions?: boolean,
  ): Promise<TechniqueDraft>;
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
    resolve: (cardId, version, conditions, ambiguities, added = []) =>
      callForKind<CardSummary & { kind: "technique_card" }>(
        target,
        `/v1/technique-cards/${encodeURIComponent(cardId)}/resolutions`,
        "technique_card",
        { body: { version, conditions, ambiguities, added } },
      ),
    recheck: (cardId) =>
      callForKind<CardSummary & { kind: "technique_card" }>(
        target,
        `/v1/technique-cards/${encodeURIComponent(cardId)}/recheck`,
        "technique_card",
        { method: "POST" },
      ),
    settings: () =>
      callForKind<TechniqueSettings>(
        target,
        "/v1/technique-settings",
        "technique_settings",
      ),
    setDefaultModel: (model) =>
      callForKind<TechniqueSettings>(
        target,
        "/v1/technique-settings",
        "technique_settings",
        {
          method: "PUT",
          body: { default_model: model },
        },
      ),
    startDraft: (cardId, model) =>
      callForKind<{ kind: "technique_card_draft_job"; draft_id: string }>(
        target,
        `/v1/technique-cards/${encodeURIComponent(cardId)}/drafts`,
        "technique_card_draft_job",
        { body: { model } },
      ),
    draft: (draftId) =>
      callForKind<TechniqueDraft>(
        target,
        `/v1/technique-drafts/${encodeURIComponent(draftId)}`,
        "technique_card_draft",
      ),
    drafts: async (cardId) =>
      (
        await callForKind<{
          kind: "technique_card_drafts";
          drafts: TechniqueDraft[];
        }>(
          target,
          `/v1/technique-cards/${encodeURIComponent(cardId)}/drafts`,
          "technique_card_drafts",
        )
      ).drafts,
    saveDraft: (
      draftId,
      name,
      acceptUnconvertible,
      confirmAssumptions = false,
    ) =>
      callForKind<TechniqueDraft>(
        target,
        `/v1/technique-drafts/${encodeURIComponent(draftId)}/save`,
        "technique_card_draft",
        {
          body: {
            name,
            accept_unconvertible: acceptUnconvertible,
            confirm_assumptions: confirmAssumptions,
          },
        },
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
