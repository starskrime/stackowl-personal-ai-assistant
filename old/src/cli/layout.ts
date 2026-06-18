export interface Layout {
  cols: number;
  rows: number;
  leftW: number;
  rightW: number;
}

/** Compute terminal layout from given dimensions (defaults to process.stdout). */
export function computeLayout(rawCols?: number, rawRows?: number): Layout {
  const cols  = Math.max(rawCols  ?? process.stdout.columns ?? 100, 80);
  const rows  = Math.max(rawRows  ?? process.stdout.rows    ?? 30,  20);
  const leftW = Math.max(32, Math.floor((cols - 4) * 0.38));
  const rightW = cols - 4 - leftW;
  return { cols, rows, leftW, rightW };
}
