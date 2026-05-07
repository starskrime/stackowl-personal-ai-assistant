import { readFile, writeFile, mkdir } from "node:fs/promises";
import { dirname } from "node:path";
import { homedir } from "node:os";
import { existsSync } from "node:fs";

export type BackendName = "camofox" | "scrapling" | "live-browser" | "puppeteer";

export interface BackendStatus {
  installed: boolean;
  version?: string;
  lastProbe: string;
  ready: boolean;
  lastError?: string;
}

export type AvailabilityMap = Record<BackendName, BackendStatus>;

export type ProbeFn = () => Promise<Partial<BackendStatus>>;
export type ProbeMap = Record<BackendName, ProbeFn>;

const DEFAULT_PATH = `${homedir()}/.stackowl/runtime-availability.json`;

function emptyStatus(): BackendStatus {
  return { installed: false, ready: false, lastProbe: new Date(0).toISOString() };
}
function emptyMap(): AvailabilityMap {
  return { camofox: emptyStatus(), scrapling: emptyStatus(), "live-browser": emptyStatus(), puppeteer: emptyStatus() };
}

export class RuntimeAvailability {
  constructor(private path: string = DEFAULT_PATH, private probes?: ProbeMap) {}

  async load(): Promise<AvailabilityMap> {
    if (!existsSync(this.path)) {
      const fresh = emptyMap();
      await this.write(fresh);
      return fresh;
    }
    try {
      const raw = await readFile(this.path, "utf8");
      const parsed = JSON.parse(raw) as Partial<AvailabilityMap>;
      return { ...emptyMap(), ...parsed };
    } catch {
      const fresh = emptyMap();
      await this.write(fresh);
      return fresh;
    }
  }

  async update(backend: BackendName, status: Partial<BackendStatus>): Promise<void> {
    const map = await this.load();
    map[backend] = { ...map[backend], ...status, lastProbe: status.lastProbe ?? new Date().toISOString() };
    await this.write(map);
  }

  async isReady(backend: BackendName): Promise<boolean> {
    const map = await this.load();
    return Boolean(map[backend]?.ready);
  }

  async probeAll(): Promise<AvailabilityMap> {
    const map = await this.load();
    if (!this.probes) return map;
    const now = new Date().toISOString();
    for (const name of Object.keys(this.probes) as BackendName[]) {
      try {
        const partial = await this.probes[name]();
        map[name] = { ...map[name], ...partial, lastProbe: now };
      } catch (err) {
        map[name] = { ...map[name], installed: false, ready: false, lastProbe: now,
                      lastError: err instanceof Error ? err.message : String(err) };
      }
    }
    await this.write(map);
    return map;
  }

  private async write(map: AvailabilityMap): Promise<void> {
    const dir = dirname(this.path);
    if (!existsSync(dir)) await mkdir(dir, { recursive: true });
    await writeFile(this.path, JSON.stringify(map, null, 2), "utf8");
  }
}
