/**
 * Config patch API — deep-merge a section of the live config, persist atomically,
 * and fire the reload bus for hot-reloadable sections.
 *
 * Lives in its own module (separate from loader.ts) so tests can spy on
 * saveConfig at the import boundary.
 */

import { saveConfig } from "./loader.js";
import { configReloadBus } from "./reload-bus.js";
import type { StackOwlConfig } from "./loader.js";

export type DeepPartial<T> = T extends object
  ? { [K in keyof T]?: DeepPartial<T[K]> }
  : T;

/**
 * Deep-merge a partial patch into one section of the live config, persist
 * atomically, then fire the reload bus for hot-reloadable sections.
 *
 * On save failure: rolls back the in-memory mutation and rethrows.
 * On bus handler failure: rolls back disk + memory and rethrows.
 *
 * @param liveConfig        The live config object held by OwlGateway.
 * @param section           Top-level key of StackOwlConfig.
 * @param patch             Partial update — deep-merged, not replaced.
 * @param basePath          Directory containing stackowl.config.json.
 * @param opts.restartRequired  Pass true when the change requires a process restart.
 *                              Suppresses bus emit; sets restartRequired in result.
 */
export async function patchConfig<K extends keyof StackOwlConfig>(
  liveConfig: StackOwlConfig,
  section: K,
  patch: DeepPartial<StackOwlConfig[K]>,
  basePath: string,
  opts?: { restartRequired?: boolean },
): Promise<{ hotReloaded: boolean; restartRequired: boolean }> {
  const restartRequired = opts?.restartRequired ?? false;

  // Snapshot for rollback
  const prev = JSON.parse(JSON.stringify(liveConfig[section] ?? null)) as StackOwlConfig[K];

  deepMergeSection(liveConfig, section, patch);

  try {
    await saveConfig(basePath, liveConfig);
  } catch (err) {
    liveConfig[section] = prev;
    throw err;
  }

  if (restartRequired) {
    return { hotReloaded: false, restartRequired: true };
  }

  try {
    await configReloadBus.emit(section, liveConfig[section], prev);
    return { hotReloaded: true, restartRequired: false };
  } catch (err) {
    liveConfig[section] = prev;
    await saveConfig(basePath, liveConfig).catch(() => undefined);
    throw err;
  }
}

function deepMergeSection<K extends keyof StackOwlConfig>(
  config: StackOwlConfig,
  section: K,
  patch: DeepPartial<StackOwlConfig[K]>,
): void {
  const current = config[section];
  if (
    current !== null &&
    current !== undefined &&
    typeof current === "object" &&
    !Array.isArray(current) &&
    patch !== null &&
    patch !== undefined &&
    typeof patch === "object" &&
    !Array.isArray(patch)
  ) {
    for (const [k, v] of Object.entries(patch as Record<string, unknown>)) {
      const cur = (current as Record<string, unknown>)[k];
      if (
        cur !== null &&
        cur !== undefined &&
        typeof cur === "object" &&
        !Array.isArray(cur) &&
        v !== null &&
        v !== undefined &&
        typeof v === "object" &&
        !Array.isArray(v)
      ) {
        (current as Record<string, unknown>)[k] = { ...(cur as object), ...(v as object) };
      } else {
        (current as Record<string, unknown>)[k] = v;
      }
    }
  } else {
    config[section] = patch as StackOwlConfig[K];
  }
}
