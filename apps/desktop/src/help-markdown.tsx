import { Fragment, type ReactNode } from "react";

/**
 * A small renderer for the help guide (docs/FEATURES.md): headings, paragraphs,
 * nested "-" and "1." lists, "| a | b |" tables, "---", **bold**, `code` and
 * [text](link). Everything is shown as text; no HTML in the file is interpreted and
 * links are not followed (the guide is read inside the app).
 */

export interface HelpHeading {
  level: number;
  text: string;
  id: string;
}

type Block =
  | { kind: "heading"; heading: HelpHeading }
  | { kind: "paragraph"; text: string }
  | { kind: "list"; items: ListItem[]; ordered: boolean }
  | { kind: "table"; rows: string[][] }
  | { kind: "rule" };

interface ListItem {
  text: string;
  children: ListItem[];
  ordered: boolean;
}

const LIST_LINE = /^(\s*)([-*]|\d+\.)\s+(.*)$/;

/** Heading text without inline marks, for the table of contents. */
export function plainText(text: string): string {
  return text
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1");
}

function cells(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function isTableRule(line: string): boolean {
  return /^\|?(\s*:?-+:?\s*\|)+\s*:?-*:?\s*\|?$/.test(line.trim());
}

/** Splits the guide into blocks; heading ids are "h-<n>" in document order. */
export function parseHelp(source: string): Block[] {
  const blocks: Block[] = [];
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  let paragraph: string[] = [];
  let table: string[][] = [];
  // Open list items by depth; [0] is the top level.
  let list: { items: ListItem[]; ordered: boolean } | null = null;
  let stack: { indent: number; item: ListItem }[] = [];
  let headingCount = 0;

  const flushParagraph = () => {
    if (paragraph.length > 0) {
      blocks.push({ kind: "paragraph", text: paragraph.join(" ") });
      paragraph = [];
    }
  };
  const flushTable = () => {
    if (table.length > 0) {
      blocks.push({ kind: "table", rows: table });
      table = [];
    }
  };
  const flushList = () => {
    if (list) {
      blocks.push({ kind: "list", items: list.items, ordered: list.ordered });
      list = null;
      stack = [];
    }
  };
  const flushAll = () => {
    flushParagraph();
    flushTable();
    flushList();
  };

  for (const raw of lines) {
    const line = raw.trimEnd();
    if (line.trim() === "") {
      flushParagraph();
      flushTable();
      continue;
    }
    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      flushAll();
      headingCount += 1;
      blocks.push({
        kind: "heading",
        heading: {
          level: heading[1]!.length,
          text: heading[2]!,
          id: `h-${headingCount}`,
        },
      });
      continue;
    }
    if (/^-{3,}$/.test(line.trim())) {
      flushAll();
      blocks.push({ kind: "rule" });
      continue;
    }
    if (line.trim().startsWith("|")) {
      flushParagraph();
      flushList();
      if (!isTableRule(line)) table.push(cells(line));
      continue;
    }
    const item = LIST_LINE.exec(line);
    if (item) {
      flushParagraph();
      flushTable();
      const indent = item[1]!.length;
      const ordered = /\d/.test(item[2]!);
      const node: ListItem = { text: item[3]!, children: [], ordered };
      while (stack.length > 0 && stack[stack.length - 1]!.indent >= indent) {
        stack.pop();
      }
      const parent = stack[stack.length - 1];
      if (parent) {
        parent.item.children.push(node);
      } else {
        if (!list) list = { items: [], ordered };
        list.items.push(node);
      }
      stack.push({ indent, item: node });
      continue;
    }
    // A wrapped line continues the open list item, otherwise the paragraph.
    const open = stack[stack.length - 1];
    if (open && /^\s+/.test(raw)) {
      open.item.text += ` ${line.trim()}`;
      continue;
    }
    flushList();
    flushTable();
    paragraph.push(line.trim());
  }
  flushAll();
  return blocks;
}

/** **bold**, `code` and [text](link) of one line; links show only their text. */
export function inline(text: string): ReactNode[] {
  return text
    .split(/(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]*\))/)
    .filter((part) => part !== "")
    .map((part, index) => {
      if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
        return <strong key={index}>{inline(part.slice(2, -2))}</strong>;
      }
      if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
        return <code key={index}>{part.slice(1, -1)}</code>;
      }
      const link = /^\[([^\]]+)\]\(([^)]*)\)$/.exec(part);
      if (link) {
        return (
          <span key={index} className="help-link" title={link[2]}>
            {link[1]}
          </span>
        );
      }
      return <Fragment key={index}>{part}</Fragment>;
    });
}

function renderItems(items: ListItem[], ordered: boolean): ReactNode {
  const children = items.map((item, index) => (
    <li key={index}>
      {inline(item.text)}
      {item.children.length > 0
        ? renderItems(item.children, item.children[0]!.ordered)
        : null}
    </li>
  ));
  return ordered ? <ol>{children}</ol> : <ul>{children}</ul>;
}

export function renderHelp(blocks: Block[]): ReactNode[] {
  return blocks.map((block, index) => {
    switch (block.kind) {
      case "heading": {
        const { level, text, id } = block.heading;
        const Tag = `h${Math.min(level + 1, 6)}` as "h2";
        return (
          <Tag key={index} id={id} className={`help-h help-h${level}`}>
            {inline(text)}
          </Tag>
        );
      }
      case "paragraph":
        return <p key={index}>{inline(block.text)}</p>;
      case "list":
        return (
          <Fragment key={index}>
            {renderItems(block.items, block.ordered)}
          </Fragment>
        );
      case "table": {
        const [head, ...body] = block.rows;
        return (
          <div key={index} className="help-table-wrap">
            <table className="help-table">
              <thead>
                <tr>
                  {(head ?? []).map((cell, cellIndex) => (
                    <th key={cellIndex}>{inline(cell)}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {body.map((row, rowIndex) => (
                  <tr key={rowIndex}>
                    {row.map((cell, cellIndex) => (
                      <td key={cellIndex}>{inline(cell)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      }
      case "rule":
        return <hr key={index} />;
    }
  });
}

/** Headings in order, for the table of contents. */
export function helpHeadings(blocks: Block[]): HelpHeading[] {
  return blocks.flatMap((block) =>
    block.kind === "heading" ? [block.heading] : [],
  );
}
