import { randomUUID } from "node:crypto";
import { log } from "../logger.js";
import type { OwlEngine } from "../engine/runtime.js";
import type { Session, SessionMetadata } from "./types.js";
import type { SessionStore } from "./store.js";

export interface SessionRunnerOptions {
  _pollIntervalMs?: number;
  _maxConcurrent?: number;
  _defaultTimeoutMs?: number;
  _sessionMaxAgeDays?: number;
}

interface RunHandle {
  sessionId: string;
  abortController: AbortController;
  promise: Promise<void>;
}

export class SessionRunner {
  private active = new Map<string, RunHandle>();
  private stopped = false;

  constructor(
    private readonly store: SessionStore,
    private readonly engineFactory: () => OwlEngine,
    private readonly baseContext: () => any,
    _opts: SessionRunnerOptions = {},
  ) {}

  async start(): Promise<void> {
    log.engine.info("[SessionRunner] starting");
  }

  stop(): void {
    this.stopped = true;
    for (const handle of this.active.values()) {
      handle.abortController.abort();
    }
    this.active.clear();
    log.engine.info("[SessionRunner] stopped");
  }

  async spawn(opts: {
    prompt: string;
    parentId?: string;
    metadata?: SessionMetadata;
  }): Promise<Session> {
    const id = "ses_" + randomUUID();
    const now = new Date().toISOString();
    const session: Session = {
      id,
      parentId: opts.parentId ?? null,
      status: "pending",
      prompt: opts.prompt,
      history: [],
      metadata: opts.metadata ?? {},
      createdAt: now,
      updatedAt: now,
    };
    this.store.create(session);
    log.engine.info("[SessionRunner] spawned", { id, parentId: session.parentId });

    setImmediate(() =>
      this.driveSession(id).catch((err) => {
        log.engine.error("[SessionRunner] driveSession failed", err as Error, {
          id,
        });
      }),
    );

    return session;
  }

  private async driveSession(sessionId: string): Promise<void> {
    if (this.stopped) return;
    const session = this.store.findOne(sessionId);
    if (!session) return;

    this.store.update(sessionId, { status: "running" });
    const abortController = new AbortController();
    this.active.set(sessionId, {
      sessionId,
      abortController,
      promise: Promise.resolve(),
    });

    try {
      const engine = this.engineFactory();
      const context = {
        ...this.baseContext(),
        signal: abortController.signal,
      };
      const result = await engine.run(session.prompt, context);
      this.store.update(sessionId, {
        status: "completed",
        result:
          typeof result === "string"
            ? result
            : result?.content ?? String(result),
      });
      log.engine.info("[SessionRunner] session completed", { id: sessionId });
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : String(err);
      this.store.update(sessionId, {
        status: errorMsg.includes("Abort") ? "terminated" : "failed",
        error: errorMsg,
      });
      log.engine.error("[SessionRunner] session failed", err as Error, {
        id: sessionId,
      });
    } finally {
      this.active.delete(sessionId);
    }
  }
}
