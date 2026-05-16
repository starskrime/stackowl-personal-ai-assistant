/**
 * Shared utilities for /config namespace handlers.
 */

import type { CoreCommandContext, CoreCommandResult } from "../../registry.js";
import { patchConfig } from "../../../../../config/patch.js";
import type { StackOwlConfig, DeepPartial } from "../../../../../config/loader.js";
import { log } from "../../../../../logger.js";

/** Standard success notice text. */
export function savedText(hotReloaded: boolean, restartRequired: boolean): string {
  if (restartRequired) return "✓ Saved. ⚠ Restart StackOwl to apply.";
  if (hotReloaded) return "✓ Saved.";
  return "✓ Saved.";
}

/** Apply a config patch and return a CoreCommandResult. */
export async function applyPatch<K extends keyof StackOwlConfig>(
  ctx: CoreCommandContext,
  section: K,
  patch: DeepPartial<StackOwlConfig[K]>,
  opts?: { restartRequired?: boolean },
): Promise<CoreCommandResult> {
  const live = ctx.getOwlGateway().getConfig();
  const basePath = ctx.getOwlGateway().getWorkspacePath();
  log.cli.debug(`config.${section}: applying patch`, { patch, restartRequired: opts?.restartRequired });
  try {
    const result = await patchConfig(live, section, patch, basePath, opts);
    const text = savedText(result.hotReloaded, result.restartRequired);
    log.cli.debug(`config.${section}: patch saved`, result);
    return { kind: "system-message", text };
  } catch (err) {
    log.cli.error(`config.${section}: patch failed`, err as Error);
    return { kind: "error", text: `Failed to save: ${(err as Error).message}` };
  }
}

/** Mask an API key for display. */
export function maskKey(v: string | null | undefined): string {
  if (!v) return "<unset>";
  if (v.length <= 4) return "•".repeat(v.length);
  return "…" + v.slice(-4);
}
