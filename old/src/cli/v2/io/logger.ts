/**
 * TUI v2 — console redirect
 *
 * Routes stray console.{log,warn,error,info} calls through the structured
 * logger (file sink + ring buffer) instead of raw stdout, which Ink owns.
 *
 * Also sets STACKOWL_TUI_MOUNTED=1 so the pretty-console sink stays quiet
 * (writing to raw stderr during Ink is safe, but unnecessary duplication).
 */

import { getLogger } from "../../../infra/observability/logger.js";

const _log = getLogger("console-stray");

let _installed = false;
let _origLog:   typeof console.log;
let _origWarn:  typeof console.warn;
let _origError: typeof console.error;
let _origInfo:  typeof console.info;

export function installLoggerRedirect(): void {
  if (_installed) return;
  _installed = true;

  // Signal pretty-console sink to suppress stderr output while Ink is mounted
  process.env.STACKOWL_TUI_MOUNTED = "1";

  _origLog   = console.log;
  _origWarn  = console.warn;
  _origError = console.error;
  _origInfo  = console.info;

  // Route stray console calls into the structured logger (file + ring buffer)
  // so background services don't inject raw bytes into the Ink render buffer.
  console.log   = (...args: unknown[]) => _log.info(args.map(String).join(" "));
  console.warn  = (...args: unknown[]) => _log.warn(args.map(String).join(" "));
  console.error = (...args: unknown[]) => _log.error(args.map(String).join(" "), undefined);
  console.info  = (...args: unknown[]) => _log.info(args.map(String).join(" "));
}

export function uninstallLoggerRedirect(): void {
  if (!_installed) return;
  _installed = false;
  process.env.STACKOWL_TUI_MOUNTED = "";
  console.log   = _origLog;
  console.warn  = _origWarn;
  console.error = _origError;
  console.info  = _origInfo;
}
