/**
 * StackOwl — Markdown Table Converter
 *
 * Converts markdown tables to channel-friendly text.
 * Output is plain text + markdown bold (**label:**) — NO HTML.
 * The caller (renderContent) is responsible for HTML escaping and
 * markdown-to-HTML conversion.
 *
 * Also converts other markdown constructs that don't render in Telegram:
 *   ## Heading   → **Heading**
 *   ---          → (empty line)
 *   > blockquote → _text_
 */

/**
 * Parse a markdown table from a contiguous block of pipe-delimited lines.
 */
function parseTable(lines: string[]): { headers: string[]; rows: string[][] } | null {
  if (lines.length < 3) return null;

  const parseRow = (line: string): string[] =>
    line
      .trim()
      .replace(/^\||\|$/g, "")
      .split("|")
      .map((cell) => cell.trim());

  const isSeparator = (line: string): boolean =>
    /^\|?[\s\-:]+(\|[\s\-:]+)*\|?$/.test(line.trim());

  const headers = parseRow(lines[0]);
  if (headers.length === 0 || !lines[1] || !isSeparator(lines[1])) return null;

  const rows: string[][] = [];
  for (let i = 2; i < lines.length; i++) {
    const line = lines[i].trim();
    if (!line || (!line.startsWith("|") && !line.includes("|"))) break;
    rows.push(parseRow(line));
  }

  return rows.length > 0 ? { headers, rows } : null;
}

/**
 * Format a parsed table as plain text with markdown bold.
 * 2-column: "• **H1:** val  —  **H2:** val"
 * 3+ columns: "**H1** | **H2** | **H3**" header + "• val | val | val" rows
 */
function formatTable(headers: string[], rows: string[][]): string {
  if (headers.length === 2) {
    return rows
      .map((row) => `• **${headers[0]}:** ${row[0] ?? ""}  —  **${headers[1]}:** ${row[1] ?? ""}`)
      .join("\n");
  }

  const headerLine = headers.map((h) => `**${h}**`).join(" | ");
  const dataLines = rows.map((row) =>
    "• " + headers.map((h, i) => `${h}: ${row[i] ?? ""}`).join("  |  "),
  );
  return [headerLine, ...dataLines].join("\n");
}

/**
 * Convert markdown tables and unsupported constructs to Telegram-friendly text.
 * Returns plain text + markdown bold only — no HTML tags.
 */
export function convertTables(text: string): string {
  const lines = text.split("\n");
  const output: string[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    // ── Markdown table detection ──────────────────────────────
    if (trimmed.startsWith("|")) {
      const tableLines: string[] = [];
      let j = i;
      while (j < lines.length) {
        const t = lines[j].trim();
        if (!t.startsWith("|") && !/^\|?[\s\-:]+(\|[\s\-:]+)*\|?$/.test(t)) break;
        tableLines.push(lines[j]);
        j++;
      }

      const parsed = parseTable(tableLines);
      if (parsed) {
        output.push(formatTable(parsed.headers, parsed.rows));
        i = j;
        continue;
      }
    }

    // ── ATX Headings: # Title → **Title** ────────────────────
    const headingMatch = trimmed.match(/^#{1,6}\s+(.+)/);
    if (headingMatch) {
      output.push(`**${headingMatch[1].trim()}**`);
      i++;
      continue;
    }

    // ── Thematic break: --- or *** or ___ → blank line ───────
    if (/^[-*_]{3,}$/.test(trimmed)) {
      output.push("");
      i++;
      continue;
    }

    // ── Blockquote: > text → _text_ ──────────────────────────
    const blockquoteMatch = trimmed.match(/^>\s*(.*)/);
    if (blockquoteMatch) {
      const inner = blockquoteMatch[1].trim();
      output.push(inner ? `_${inner}_` : "");
      i++;
      continue;
    }

    output.push(line);
    i++;
  }

  return output.join("\n");
}
