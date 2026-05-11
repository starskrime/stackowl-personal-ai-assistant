/**
 * SessionSaver — Writes dated markdown files to ~/.stackowl/workspace/memory/
 * when a session resets (the `/reset` command), saving the last N messages for future reference.
 */

import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { homedir } from "node:os";
import { log } from "../logger.js";

const DEFAULT_MEMORY_DIR = join(homedir(), ".stackowl", "workspace", "memory");

function pad(n: number, len = 2): string {
  return String(n).padStart(len, "0");
}

function formatDate(d: Date): { date: string; slug: string } {
  const year = d.getFullYear();
  const month = pad(d.getMonth() + 1);
  const day = pad(d.getDate());
  const hour = pad(d.getHours());
  const min = pad(d.getMinutes());
  return { date: `${year}-${month}-${day}`, slug: `${hour}${min}` };
}

export interface SessionSaverOptions {
  messageCount?: number;
}

export class SessionSaver {
  private messageCount: number;

  constructor(
    private readonly memoryDir: string = DEFAULT_MEMORY_DIR,
    options: SessionSaverOptions = {},
  ) {
    this.messageCount = options.messageCount ?? 15;
  }

  async save(
    messages: Array<{ role: string; content: string | unknown }>,
    sessionId: string,
  ): Promise<string | null> {
    if (!messages.length) return null;

    const recent = messages.slice(-this.messageCount);
    const now = new Date();
    const { date, slug } = formatDate(now);

    await mkdir(this.memoryDir, { recursive: true });

    const filename = `${date}-${slug}.md`;
    const filePath = join(this.memoryDir, filename);

    const lines: string[] = [
      `# Session: ${date} ${now.toTimeString().slice(0, 8)}`,
      ``,
      `**Session ID:** ${sessionId}`,
      `**Messages saved:** ${recent.length}`,
      ``,
      `## Conversation`,
      ``,
    ];

    for (const msg of recent) {
      const role = msg.role === "user" ? "**User**" : "**Owl**";
      lines.push(
        `${role}: ${typeof msg.content === "string" ? msg.content : JSON.stringify(msg.content)}`,
      );
      lines.push(``);
    }

    await writeFile(filePath, lines.join("\n"), "utf-8");
    log.engine.info(`[SessionSaver] Saved session to ${filePath}`, {
      messages: recent.length,
    });
    return filePath;
  }
}
