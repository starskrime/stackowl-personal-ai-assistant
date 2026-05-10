/**
 * logger.ts — routes internal log calls through output.ts.
 *
 * Replaces the console.log/console.warn monkey-patch in v1 renderer.ts:117-122.
 * Install via installLoggerRedirect() before Ink mounts.
 */

import { writeln } from "./output.js";

let _installed = false;
let _origLog: typeof console.log;
let _origWarn: typeof console.warn;

export function installLoggerRedirect(): void {
  if (_installed) return;
  _installed = true;
  _origLog = console.log;
  _origWarn = console.warn;
  // Ink already owns stdout; route stray console output through our writer
  // so background services don't inject raw bytes into the Ink render buffer.
  console.log = (...args: unknown[]) => writeln(args.map(String).join(" "));
  console.warn = (...args: unknown[]) => writeln("[warn] " + args.map(String).join(" "));
}

export function uninstallLoggerRedirect(): void {
  if (!_installed) return;
  _installed = false;
  console.log = _origLog;
  console.warn = _origWarn;
}
