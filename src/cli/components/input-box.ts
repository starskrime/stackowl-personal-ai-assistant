import chalk from "chalk";
import { AMBER, BLUE, LBL, W, PANEL_BG, SPINNER } from "../shared/palette.js";
import { visLen } from "../shared/text.js";

export interface InputBoxProps {
  buf:     string;
  cursor:  number;
  locked:  boolean;
  masked:  boolean;
  spinIdx: number;
}

const PREFIX_W = 4;      // "  › " or "    " — 4 visible chars
const MAX_CONTENT_LINES = 6;

/**
 * How many terminal rows the input box will occupy for the given buffer and box width.
 * Min 3 (border + 1 content line + border), grows up to 2 + MAX_CONTENT_LINES.
 */
export function inputBoxHeight(props: Pick<InputBoxProps, "buf" | "locked">, width: number): number {
  if (props.locked || !props.buf) return 3;
  const textW = Math.max(1, width - 2 - PREFIX_W);
  const lineCount = Math.ceil(props.buf.length / textW);
  return 2 + Math.min(MAX_CONTENT_LINES, Math.max(1, lineCount));
}

export function renderInputBox(props: InputBoxProps, width: number): string {
  const topBorder = PANEL_BG(AMBER("▔".repeat(width + 2)));
  const botBorder = PANEL_BG(AMBER("▁".repeat(width + 2)));
  const bodyLines = buildBodyLines(props, width);
  return [topBorder, ...bodyLines, botBorder].join("\n");
}

function buildBodyLines(props: InputBoxProps, width: number): string[] {
  const { buf, cursor, locked, masked, spinIdx } = props;
  const innerW = width - 2; // content area between the single-space paddings

  if (locked) {
    const lockedContent = "  " + BLUE(SPINNER[spinIdx % SPINNER.length]) + LBL("  thinking — press ESC to stop");
    const pad = " ".repeat(Math.max(0, innerW - visLen(lockedContent)));
    return [PANEL_BG(" " + lockedContent + pad + " ")];
  }

  const textW = Math.max(1, innerW - PREFIX_W);
  const display = masked ? "*".repeat(buf.length) : buf;

  // Split display text into chunks of textW chars
  const chunks: string[] = [];
  if (display.length === 0) {
    chunks.push("");
  } else {
    for (let i = 0; i < display.length; i += textW) {
      if (chunks.length >= MAX_CONTENT_LINES) break;
      chunks.push(display.slice(i, i + textW));
    }
  }

  return chunks.map((chunk, lineIdx) => {
    const chunkStart = lineIdx * textW;
    const localCursor = cursor - chunkStart;
    const isLastChunk = lineIdx === chunks.length - 1;
    const prefix = lineIdx === 0 ? "  " + AMBER("› ") : "    ";

    let renderedText: string;
    let extraCursorChar = false;

    if (display.length === 0) {
      renderedText = chalk.bgYellow.black(" ");
      extraCursorChar = true;
    } else if (localCursor < 0 || localCursor > chunk.length) {
      renderedText = W(chunk);
    } else if (localCursor === chunk.length) {
      if (isLastChunk) {
        renderedText = W(chunk) + chalk.bgYellow.black(" ");
        extraCursorChar = true;
      } else {
        renderedText = W(chunk); // cursor will show at start of next line
      }
    } else {
      renderedText = W(chunk.slice(0, localCursor))
        + chalk.bgYellow.black(chunk[localCursor]!)
        + W(chunk.slice(localCursor + 1));
    }

    const usedChars = chunk.length + (extraCursorChar ? 1 : 0);
    const pad = " ".repeat(Math.max(0, textW - usedChars));
    return PANEL_BG(" " + prefix + renderedText + pad + " ");
  });
}
