import { Fragment, type ReactNode } from "react";

/** **bold** spans of a line; everything else stays plain text (never HTML). */
function bold(line: string): ReactNode[] {
  return line
    .split(/(\*\*[^*]+\*\*)/)
    .map((part, index) =>
      part.startsWith("**") && part.endsWith("**") && part.length > 4 ? (
        <strong key={index}>{part.slice(2, -2)}</strong>
      ) : (
        <Fragment key={index}>{part}</Fragment>
      ),
    );
}

/** Cells of a "| a | b |" table line. */
function cells(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

/**
 * The markdown forms answers use: **bold**, "- " bullet lines and "| a | b |" tables.
 * Everything is shown as text; no HTML from the answer is ever interpreted.
 */
export function answerText(text: string): ReactNode[] {
  const blocks: ReactNode[] = [];
  let bullets: string[] = [];
  let table: string[][] = [];
  const flush = () => {
    if (bullets.length > 0) {
      blocks.push(
        <ul key={`list-${blocks.length}`}>
          {bullets.map((item, index) => (
            <li key={index}>{bold(item)}</li>
          ))}
        </ul>,
      );
      bullets = [];
    }
    if (table.length > 0) {
      const [head, ...body] = table;
      blocks.push(
        <table key={`table-${blocks.length}`} className="bt-table">
          <thead>
            <tr>
              {(head ?? []).map((cell, index) => (
                <th key={index}>{bold(cell)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {body.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {row.map((cell, index) => (
                  <td key={index}>{bold(cell)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>,
      );
      table = [];
    }
  };
  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    if (trimmed.startsWith("|") && trimmed.endsWith("|")) {
      if (bullets.length > 0) flush();
      // The "|---|---|" line under the header carries no content.
      if (!/^\|[\s:|-]+\|$/.test(trimmed)) table.push(cells(trimmed));
      continue;
    }
    if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
      if (table.length > 0) flush();
      bullets.push(trimmed.slice(2));
      continue;
    }
    flush();
    if (trimmed) blocks.push(<p key={`p-${blocks.length}`}>{bold(trimmed)}</p>);
  }
  flush();
  return blocks;
}
