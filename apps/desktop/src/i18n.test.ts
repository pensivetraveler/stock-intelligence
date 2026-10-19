import { describe, expect, it } from "vitest";

import {
  detectLanguage,
  DICTIONARIES,
  LANGUAGE_STORAGE_KEY,
  translateDynamic,
  translator,
  type MessageKey,
} from "./i18n";

function placeholders(text: string): string[] {
  return [...text.matchAll(/\{(\w+)\}/g)].map((match) => match[1]).sort();
}

function memoryStorage(initial: Record<string, string> = {}): Storage {
  const items = new Map(Object.entries(initial));
  return {
    get length() {
      return items.size;
    },
    clear: () => items.clear(),
    getItem: (key) => items.get(key) ?? null,
    key: (index) => [...items.keys()][index] ?? null,
    removeItem: (key) => void items.delete(key),
    setItem: (key, value) => void items.set(key, value),
  };
}

describe("i18n", () => {
  it("uses the same placeholders in every language", () => {
    for (const key of Object.keys(DICTIONARIES.en) as MessageKey[]) {
      expect(placeholders(DICTIONARIES.ko[key]), key).toEqual(
        placeholders(DICTIONARIES.en[key]),
      );
    }
  });

  it("fills placeholders and leaves unknown ones visible", () => {
    const t = translator("ko");
    expect(t("ws.chartTitle", { n: 2 })).toBe("차트 2");
    expect(translator("en")("ws.chartTitle")).toBe("Chart {n}");
    expect(translateDynamic(t, "reason.provider_no_bar", "x")).toBe(
      "체결 없음",
    );
    expect(translateDynamic(t, "reason.new_reason", "new reason")).toBe(
      "new reason",
    );
  });

  it("prefers the saved choice, then the browser language", () => {
    expect(
      detectLanguage(memoryStorage({ [LANGUAGE_STORAGE_KEY]: "ko" })),
    ).toBe("ko");
    expect(detectLanguage(memoryStorage())).toBe("en"); // jsdom reports en-US
    expect(
      detectLanguage(memoryStorage({ [LANGUAGE_STORAGE_KEY]: "fr" })),
    ).toBe("en");
  });
});
