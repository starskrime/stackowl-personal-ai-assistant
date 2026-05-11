/**
 * StackOwl Observability — Back-compat shim
 *
 * Re-creates the legacy `log` singleton object and `initFileLog` export
 * on top of the new logger so all 205 importing files work unchanged.
 *
 * src/logger.ts becomes a thin re-export of this file.
 */

import { getLogger } from "./logger.js";
import { initFileLog as _initFileLog } from "./sinks/jsonl-file.js";

export type { LogLevel } from "./schema.js";
export { Logger } from "./logger.js";

/** Drop-in replacement for initFileLog(workspacePath). */
export function initFileLog(workspacePath: string): void {
  _initFileLog(workspacePath);
}

/**
 * Legacy `log` singleton — all module singletons preserved verbatim.
 * Each member is a full Logger instance with back-compat helpers.
 */
export const log = {
  telegram:  getLogger("telegram"),
  slack:     getLogger("slack"),
  cli:       getLogger("cli"),
  engine:    getLogger("engine"),
  tool:      getLogger("tool"),
  evolution: getLogger("evolution"),
  memory:    getLogger("memory"),
  heartbeat: getLogger("heartbeat"),
  pellet:    getLogger("pellet"),
  parliament: getLogger("parliament"),
  gateway:   getLogger("gateway"),
  cognition: getLogger("cognition"),
};
