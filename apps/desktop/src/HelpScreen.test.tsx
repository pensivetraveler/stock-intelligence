import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { HelpScreen } from "./HelpScreen";
import { helpHeadings, parseHelp } from "./help-markdown";
import { I18nProvider } from "./I18nProvider";

const SAMPLE = `# 구현 기능 총정리

- **정리일:** 2026-11-15
- **읽는 법:** 아래 표시를
  이어지는 줄과 함께 본다.
  - **[실확인]** 실제로 확인했다.

---

## 1. 전체 구성

| 구성 요소 | 내용 |
| --- | --- |
| **API** | \`127.0.0.1:8765\` |

## 2. 시장 데이터

자세한 근거는 [PROJECT_STATE.md](PROJECT_STATE.md)를 본다. <b>태그</b>는 글자로 보인다.

1. 첫째
2. 둘째
`;

function memoryStorage(language: string): Storage {
  const values = new Map([["siw.language", language]]);
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => void values.set(key, value),
    removeItem: (key) => void values.delete(key),
    clear: () => values.clear(),
    key: () => null,
    length: values.size,
  };
}

function renderHelp(onClose = vi.fn(), language = "ko") {
  render(
    <I18nProvider storage={memoryStorage(language)}>
      <HelpScreen onClose={onClose} source={SAMPLE} />
    </I18nProvider>,
  );
  return onClose;
}

describe("help guide", () => {
  it("parses headings, nested lists, wrapped lines and tables", () => {
    const blocks = parseHelp(SAMPLE);
    expect(helpHeadings(blocks).map((heading) => heading.text)).toEqual([
      "구현 기능 총정리",
      "1. 전체 구성",
      "2. 시장 데이터",
    ]);
    const list = blocks.find((block) => block.kind === "list");
    expect(list).toMatchObject({
      kind: "list",
      items: [
        { text: "**정리일:** 2026-11-15", children: [] },
        {
          text: "**읽는 법:** 아래 표시를 이어지는 줄과 함께 본다.",
          children: [{ text: "**[실확인]** 실제로 확인했다." }],
        },
      ],
    });
    expect(blocks.find((block) => block.kind === "table")).toEqual({
      kind: "table",
      rows: [
        ["구성 요소", "내용"],
        ["**API**", "`127.0.0.1:8765`"],
      ],
    });
  });

  it("shows the guide with a table of contents and never renders HTML", () => {
    renderHelp();
    const dialog = screen.getByRole("dialog", {
      name: "도움말 · 구현 기능 안내",
    });
    const toc = within(dialog).getByRole("navigation", { name: "목차" });
    expect(
      within(toc)
        .getAllByRole("button")
        .map((button) => button.textContent),
    ).toEqual(["1. 전체 구성", "2. 시장 데이터"]);
    expect(within(dialog).getByRole("table")).toHaveTextContent(
      "127.0.0.1:8765",
    );
    expect(within(dialog).getByText("PROJECT_STATE.md")).toHaveClass(
      "help-link",
    );
    expect(dialog.querySelector("b")).toBeNull();
    expect(dialog).toHaveTextContent("<b>태그</b>는 글자로 보인다.");
    expect(dialog.querySelectorAll("ol li")).not.toHaveLength(0);
  });

  it("closes with the button and with Escape", () => {
    const onClose = renderHelp();
    fireEvent.click(screen.getByRole("button", { name: "닫기" }));
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("is a modal dialog that starts on the contents and closes with Escape inside", () => {
    const onClose = renderHelp();
    const dialog = screen.getByRole("dialog", {
      name: "도움말 · 구현 기능 안내",
    });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    // The first contents link takes focus, not the close button.
    expect(document.activeElement).toHaveClass("help-toc-link");
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("notes that the guide is in Korean when the screen is in English", () => {
    renderHelp(vi.fn(), "en");
    expect(
      screen.getByText("This guide is written in Korean."),
    ).toBeInTheDocument();
  });

  it("bundles docs/FEATURES.md by default", () => {
    render(
      <I18nProvider storage={memoryStorage("ko")}>
        <HelpScreen onClose={vi.fn()} />
      </I18nProvider>,
    );
    expect(
      screen.getByRole("heading", { name: "구현 기능 총정리" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "9. SIW Agent 주문 실행" }),
    ).toBeInTheDocument();
  });
});
