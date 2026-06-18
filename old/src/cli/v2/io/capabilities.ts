/** Terminal capability detection. */

export interface TerminalCapabilities {
  isTTY: boolean;
  columns: number;
  rows: number;
  hasColor: boolean;
  hasTrueColor: boolean;
}

export function detectCapabilities(): TerminalCapabilities {
  const isTTY = Boolean(process.stdout.isTTY);
  const columns = process.stdout.columns ?? 80;
  const rows = process.stdout.rows ?? 24;
  const colorTerm = process.env.COLORTERM ?? "";
  const term = process.env.TERM ?? "";
  const hasColor = isTTY && (term.includes("color") || colorTerm.length > 0 || term === "xterm-256color");
  const hasTrueColor = colorTerm === "truecolor" || colorTerm === "24bit";
  return { isTTY, columns, rows, hasColor, hasTrueColor };
}
