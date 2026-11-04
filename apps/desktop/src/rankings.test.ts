import { describe, expect, it } from "vitest";

import {
  groupDigits,
  rankedValue,
  ratioPercent,
  supportsRealtime,
} from "./rankings";

describe("rankings", () => {
  it("groups digits without changing them", () => {
    expect(groupDigits("1041436650000")).toBe("1,041,436,650,000");
    expect(groupDigits("131.38")).toBe("131.38");
    expect(groupDigits("-1234.5678")).toBe("-1,234.5678");
    expect(groupDigits("0")).toBe("0");
  });

  it("shows a ratio as a signed percentage", () => {
    expect(ratioPercent("0.0125")).toEqual({ text: "+1.25%", rising: true });
    expect(ratioPercent("-0.0566")).toEqual({ text: "-5.66%", rising: false });
    expect(ratioPercent("0")).toEqual({ text: "0.00%", rising: true });
  });

  it("orders each ranking by its own value", () => {
    expect(rankedValue("MARKET_TRADING_VOLUME")).toBe("trading_volume");
    expect(rankedValue("TOP_GAINERS")).toBe("trading_amount");
    expect(rankedValue("NET_SELL")).toBe("net_amount");
    expect(supportsRealtime("TOP_LOSERS")).toBe(false);
    expect(supportsRealtime("TOSS_SECURITIES_TRADING_AMOUNT")).toBe(true);
  });
});
