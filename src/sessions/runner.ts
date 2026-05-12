import { randomUUID } from "node:crypto";
import { log } from "../logger.js";
import type { OwlEngine } from "../engine/runtime.js";
import type { Session, SessionMessage, SessionMetadata, SessionStatus } from "./types.js";
import type { SessionStore } from "./store.js";

export interface SessionRunnerOptions {
  pollIntervalMs?: number;
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
  private opts: SessionRunnerOptions;

  constructor(
    private readonly store: SessionStore,
    private readonly engineFactory: () => OwlEngine,
    private readonly baseContext: () => any,
    opts: SessionRunnerOptions = {},
  ) {
    this.opts = opts;
  }

  async start(): Promise<void> {
    log.engine.info("[SessionRunner] starting — hydrating non-terminal sessions");
    const active = this.store.list({ status: "running" });
    const pending = this.store.list({ status: "pending" });
    let resumed = 0;
    for (const session of [...active, ...pending]) {
      setImmediate(() =>
        this.driveSession(session.id).catch((err) => {
          log.engine.error("[SessionRunner] hydrated session failed", err as Error, {
            id: session.id,
          });
        }),
      );
      resumed++;
    }
    log.engine.info("[SessionRunner] hydration complete", { resumed });
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

  terminate(sessionId: string): { terminated: boolean; previousStatus: SessionStatus } {
    const session = this.store.findOne(sessionId);
    if (!session) {
      return { terminated: false, previousStatus: "failed" };
    }
    const previousStatus = session.status;
    const isTerminal = ["completed", "terminated", "failed"].includes(previousStatus);

    // Abort any in-flight run
    const handle = this.active.get(sessionId);
    if (handle) {
      handle.abortController.abort();
      this.active.delete(sessionId);
    }

    if (!isTerminal) {
      this.store.update(sessionId, {
        status: "terminated",
        terminatedAt: new Date().toISOString(),
      });
      log.engine.info("[SessionRunner] terminated", { id: sessionId, previousStatus });
    }
    return { terminated: true, previousStatus };
  }

  enqueueMessage(sessionId: string, content: string): SessionMessage {
    const session = this.store.findOne(sessionId);
    if (!session) {
      throw new Error(`Session "${sessionId}" not found`);
    }
    const msg = this.store.appendMessage(sessionId, "to_session", content);
    log.engine.debug("[SessionRunner] message enqueued", { sessionId, messageId: msg.id });
    return msg;
  }

  async awaitNextEvent(
    sessionId: string,
    timeoutMs: number,
  ): Promise<{
    ready: boolean;
    status: SessionStatus;
    newMessages: SessionMessage[];
  }> {
    const start = Date.now();
    const POLL_MS = this.opts.pollIntervalMs ?? 250;

    // Quick check
    const initial = this.store.pendingMessages(sessionId, "from_session");
    const session0 = this.store.findOne(sessionId);
    if (!session0) {
      return { ready: false, status: "failed", newMessages: [] };
    }
    if (
      initial.length > 0 ||
      ["completed", "terminated", "failed"].includes(session0.status)
    ) {
      for (const m of initial) this.store.markConsumed(m.id);
      return { ready: true, status: session0.status, newMessages: initial };
    }

    // Poll
    while (Date.now() - start < timeoutMs) {
      await new Promise((r) => setTimeout(r, POLL_MS));
      const session = this.store.findOne(sessionId);
      if (!session) {
        return { ready: false, status: "failed", newMessages: [] };
      }
      const msgs = this.store.pendingMessages(sessionId, "from_session");
      const terminal = ["completed", "terminated", "failed"].includes(
        session.status,
      );
      if (msgs.length > 0 || terminal) {
        for (const m of msgs) this.store.markConsumed(m.id);
        return { ready: true, status: session.status, newMessages: msgs };
      }
    }
    const session = this.store.findOne(sessionId)!;
    return { ready: false, status: session.status, newMessages: [] };
  }
}
