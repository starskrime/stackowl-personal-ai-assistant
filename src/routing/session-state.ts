import { readFile, writeFile, mkdir, unlink } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join, dirname } from "node:path";

export interface SessionState {
  activeOwlName: string;
  taskSummary?: string;
  pinnedAt: string;
}

export class SessionStateStore {
  constructor(private readonly workspacePath: string) {}

  private filePath(userId: string): string {
    return join(this.workspacePath, "sessions", `${userId}.json`);
  }

  async load(userId: string): Promise<SessionState | null> {
    const path = this.filePath(userId);
    if (!existsSync(path)) return null;
    try {
      const raw = await readFile(path, "utf-8");
      return JSON.parse(raw) as SessionState;
    } catch {
      return null;
    }
  }

  async save(userId: string, state: SessionState): Promise<void> {
    const path = this.filePath(userId);
    await mkdir(dirname(path), { recursive: true });
    await writeFile(path, JSON.stringify(state, null, 2), "utf-8");
  }

  async clear(userId: string): Promise<void> {
    const path = this.filePath(userId);
    if (existsSync(path)) {
      await unlink(path);
    }
  }
}
