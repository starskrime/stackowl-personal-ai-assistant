import { tmpdir as osTempdir, homedir as osHomedir } from "node:os";
import { realpathSync } from "node:fs";
import { resolve, sep } from "node:path";
import envPaths from "env-paths";
import type { Paths } from "../types.js";

const DEFAULT_APP_NAME = "stackowl";

export class PathsImpl implements Paths {
  private readonly resolvedTempdir: string;
  private readonly defaultAppName: string;

  constructor(defaultAppName: string = DEFAULT_APP_NAME) {
    this.defaultAppName = defaultAppName;
    // Resolve once at construction. macOS tmpdir is /var/folders/... but
    // realpath gives /private/var/folders/... — both must match later boundary
    // checks, so we normalize at the source.
    try {
      this.resolvedTempdir = realpathSync(osTempdir());
    } catch {
      this.resolvedTempdir = osTempdir();
    }
  }

  tempdir(): string {
    return this.resolvedTempdir;
  }

  home(): string {
    return osHomedir();
  }

  configDir(appName: string = this.defaultAppName): string {
    return envPaths(appName, { suffix: "" }).config;
  }

  cacheDir(appName: string = this.defaultAppName): string {
    return envPaths(appName, { suffix: "" }).cache;
  }

  dataDir(appName: string = this.defaultAppName): string {
    return envPaths(appName, { suffix: "" }).data;
  }

  logDir(appName: string = this.defaultAppName): string {
    return envPaths(appName, { suffix: "" }).log;
  }

  isInside(child: string, root: string): boolean {
    const resolvedChild = resolve(child);
    const resolvedRoot = resolve(root);
    return (
      resolvedChild === resolvedRoot ||
      resolvedChild.startsWith(resolvedRoot + sep)
    );
  }
}
