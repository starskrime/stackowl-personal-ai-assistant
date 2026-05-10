/**
 * Bracketed-paste support.
 *
 * Terminals that support xterm-style bracketed paste wrap pasted text in
 * ESC[200~ ... ESC[201~. This module enables the mode on entry and strips
 * the markers from input, delivering paste as a single chunk.
 */

const PASTE_START = "\x1b[200~";
const PASTE_END = "\x1b[201~";

export function enableBracketedPaste(): void {
  if (process.stdout.isTTY) process.stdout.write("\x1b[?2004h");
}

export function disableBracketedPaste(): void {
  if (process.stdout.isTTY) process.stdout.write("\x1b[?2004l");
}

/** Strip paste markers from a raw input chunk. Returns cleaned text. */
export function stripPasteMarkers(input: string): string {
  return input.replace(PASTE_START, "").replace(PASTE_END, "");
}

export function isPasteChunk(input: string): boolean {
  return input.startsWith(PASTE_START);
}
