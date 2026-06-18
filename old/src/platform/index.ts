/**
 * StackOwl Platform Layer — public entrypoint
 *
 * Consumers do:
 *   import { platform } from "../platform/index.js";           // singleton
 *   import { createPlatform } from "../platform/index.js";     // for test isolation
 *   import type { SandboxPolicy, NotifyOptions } from "../platform/index.js";
 *
 * The singleton must be initialized once at app startup:
 *   await platform.initialize();
 */
export { createPlatform } from "./registry.js";
export type {
  Platform,
  Paths,
  Sandbox, SandboxPolicy, SandboxResult,
  Notifier, NotifyOptions, NotifyResult, NotifierCapabilities,
  ProcessAPI, ProcessInfo,
  Shell, SpawnOptions, SpawnResult,
  Opener,
  SystemInfo, SystemInfoAPI, SystemCapabilities, PlatformName,
} from "./types.js";
export { PlatformError, type PlatformErrorCode } from "./errors.js";

import { createPlatform } from "./registry.js";

/** Process-wide singleton. Call `platform.initialize()` at startup. */
export const platform = createPlatform();
