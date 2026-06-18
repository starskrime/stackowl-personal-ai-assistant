/**
 * output.ts — the ONE writer.
 *
 * All output to process.stdout must go through this module.
 * The lint rule enforces this at the codebase level.
 */

export function write(text: string): void {
  process.stdout.write(text);
}

export function writeln(text: string): void {
  process.stdout.write(text + "\n");
}

/** Erase current line and return carriage. Used by Ink's render loop. */
export function clearLine(): void {
  process.stdout.write("\r\x1b[K");
}
