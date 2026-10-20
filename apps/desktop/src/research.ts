/**
 * The Toss research tables the Research panel offers, with each dataset's documented
 * options (official OpenAPI 1.2.24). Values are passed to the API as given.
 */
export type ResearchDatasetId =
  | "rankings"
  | "sector_rankings"
  | "market_indicator_prices"
  | "market_indicator_candles"
  | "market_indicator_investor_trading"
  | "sectors"
  | "sector"
  | "sector_stocks"
  | "sector_etfs"
  | "stock_investor_trading"
  | "stock_program_trades"
  | "stock_short_selling"
  | "stock_credit_trades"
  | "stock_securities_lending";

export interface ResearchOption {
  name: string;
  values: readonly string[];
}

export interface ResearchDataset {
  id: ResearchDatasetId;
  /** "symbol" or "sector": the dataset is about one; "indicator": a market indicator. */
  key?: "symbol" | "sector" | "indicator";
  options: readonly ResearchOption[];
  /** Query parameter that takes `next_cursor` for older rows. */
  cursor?: "until" | "before";
  /** Korean-market only (KRX six-character codes). */
  koreanOnly?: boolean;
}

const MARKET = { name: "marketCountry", values: ["US", "KR"] } as const;
const DURATIONS = ["1d", "1w", "1mo", "3mo", "6mo", "1y"] as const;
export const INDICATORS = [
  "KOSPI",
  "KOSDAQ",
  "KR_BOND_2Y",
  "KR_BOND_3Y",
  "KR_BOND_5Y",
  "KR_BOND_10Y",
  "KR_BOND_20Y",
  "KR_BOND_30Y",
] as const;
const TREND = { cursor: "until", koreanOnly: true, key: "symbol" } as const;

export const RESEARCH_DATASETS: readonly ResearchDataset[] = [
  {
    id: "rankings",
    options: [
      {
        name: "type",
        values: [
          "MARKET_TRADING_AMOUNT",
          "MARKET_TRADING_VOLUME",
          "TOP_GAINERS",
          "TOP_LOSERS",
          "TOSS_SECURITIES_TRADING_AMOUNT",
          "TOSS_SECURITIES_TRADING_VOLUME",
        ],
      },
      MARKET,
      // TOP_GAINERS / TOP_LOSERS do not support realtime (Toss answers 400).
      { name: "duration", values: ["1d", "realtime", ...DURATIONS.slice(1)] },
    ],
  },
  {
    id: "sector_rankings",
    options: [
      { name: "type", values: ["TOP_GAINERS", "MARKET_TRADING_AMOUNT"] },
      MARKET,
      { name: "duration", values: DURATIONS },
    ],
  },
  { id: "market_indicator_prices", options: [] },
  {
    id: "market_indicator_candles",
    key: "indicator",
    cursor: "before",
    // Minute candles exist for KOSPI and KOSDAQ only.
    options: [{ name: "interval", values: ["1d", "1m"] }],
  },
  {
    id: "market_indicator_investor_trading",
    key: "indicator",
    cursor: "until",
    options: [{ name: "interval", values: ["1d", "1w", "1mo", "1y"] }],
  },
  { id: "sectors", options: [] },
  { id: "sector", key: "sector", options: [MARKET] },
  { id: "sector_stocks", key: "sector", options: [MARKET] },
  { id: "sector_etfs", key: "sector", options: [MARKET] },
  { id: "stock_investor_trading", options: [], ...TREND },
  { id: "stock_program_trades", options: [], ...TREND },
  { id: "stock_short_selling", options: [], ...TREND },
  { id: "stock_credit_trades", options: [], ...TREND },
  { id: "stock_securities_lending", options: [], ...TREND },
];

export function datasetById(id: string): ResearchDataset {
  return (
    RESEARCH_DATASETS.find((dataset) => dataset.id === id) ??
    RESEARCH_DATASETS[0]
  );
}

/** Query parameters for one request, from the chosen options. */
export function researchParams(
  dataset: ResearchDataset,
  choices: Record<string, string>,
  cursor: string | null,
): Record<string, string> {
  const params: Record<string, string> = {};
  for (const option of dataset.options) {
    params[option.name] = choices[option.name] ?? option.values[0];
  }
  if (dataset.id === "market_indicator_prices") {
    params.symbols = INDICATORS.join(",");
  }
  if (cursor && dataset.cursor) params[dataset.cursor] = cursor;
  return params;
}
