/** SIW 디자인 토큰을 캔버스 차트(lightweight-charts)처럼 CSS 변수를 읽지 못하는 곳에 넘기는 값.
 * tokens.css 와 값이 같아야 한다(디자인-siw-3 r3, design-studio designs/stock-intelligence/siw-3/system/). */
import { ColorType, type IChartApi } from "lightweight-charts";

import { currentTheme, THEME_EVENT, type ThemeId } from "./theme-choice";

/** 테마 a(무채 크롬, 기본) 값 — 시장·원장 색은 두 테마가 같다. */
export const DECK = {
  canvas: "#0c0e11",
  panel: "#0c0e11",
  raised: "#111317",
  rule: "#22262c",
  grid: "#1b1f24",
  ink: "#e6e8eb",
  inkDim: "#a3abb4",
  accent: "#e6e8eb",
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

interface Chrome {
  background: string;
  text: string;
  grid: string;
  border: string;
}

/** 테마별 차트 바탕·글자·격자·축 경계 */
export const CHART_THEMES: Record<ThemeId, Chrome> = {
  a: {
    background: "#0c0e11",
    text: "#a3abb4",
    grid: "#1b1f24",
    border: "#22262c",
  },
  b: {
    background: "#07080a",
    text: "#a0a8b2",
    grid: "#1d2026",
    border: "#252930",
  },
};

/** 테마 a 차트 틀(모듈 상수로 옵션을 만드는 차트용) */
export const CHART_CHROME = CHART_THEMES.a;

/** 지금 테마의 차트 틀 */
export function chartChrome(): Chrome {
  return CHART_THEMES[currentTheme()];
}

/** createChart·applyOptions 에 넘기는 틀 옵션 */
export function chromeOptions(chrome: Chrome = chartChrome()) {
  return {
    layout: {
      background: { type: ColorType.Solid, color: chrome.background },
      textColor: chrome.text,
    },
    grid: {
      vertLines: { color: chrome.grid },
      horzLines: { color: chrome.grid },
    },
    rightPriceScale: { borderColor: chrome.border },
    timeScale: { borderColor: chrome.border },
  } as const;
}

/** 설정에서 테마를 바꾸면 이 차트의 틀 색을 다시 넘긴다. 돌려준 함수로 그만 듣는다. */
export function followTheme(
  chart: Pick<IChartApi, "applyOptions">,
): () => void {
  const recolour = () => chart.applyOptions(chromeOptions());
  window.addEventListener(THEME_EVENT, recolour);
  return () => window.removeEventListener(THEME_EVENT, recolour);
}
