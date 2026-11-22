/** SIW 디자인 시스템 Deck — 캔버스 차트(lightweight-charts)처럼 CSS 변수를 읽지 못하는 곳에 넘기는 값.
 * styles.css 맨 위 :root 의 --siw-* 와 값이 같아야 한다(디자인-siw-1 r4, design-studio rev/r2/d/tokens.json). */
export const DECK = {
  canvas: "#0d0f12",
  panel: "#14171c",
  raised: "#1a1e24",
  rule: "#232830",
  grid: "#1a1e24",
  ink: "#e4e7eb",
  inkDim: "#9ca4ae",
  accent: "#9a90ff",
  alert: "#ff9ba7",
  up: "#2fc07a",
  down: "#ff6878",
} as const;

/** 원장 다섯 종류 색 — 서로, 그리고 시장색·강조색과 갈린다(수동 원·가상 네모 모양 구분은 그대로). */
export const LEDGER_COLORS = {
  manual: "#f2c14e",
  imported: "#36c5d6",
  broker: "#ff9248",
  hypothetical: "#f070c8",
  backtest: "#93a4b8",
} as const;

/** 차트 바탕·글자·격자·축 경계 */
export const CHART_CHROME = {
  background: DECK.canvas,
  text: DECK.inkDim,
  grid: DECK.grid,
  border: DECK.rule,
} as const;
