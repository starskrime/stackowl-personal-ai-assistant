/**
 * StackOwl — Element 7 T16 — FactEnvelopeStore (FPC working memory)
 *
 * Wraps each tool output with its provenance metadata so downstream
 * verifiers can trace facts back to their source — and so retraction (T17)
 * can strip suspect facts from the ContextPipeline on the next turn.
 *
 * This store stays in working memory only. Provenance metadata is
 * deliberately *not* included in the rendered LLM prompt: shipping the full
 * envelope on every turn would cost ~4KB context tax per turn for no
 * runtime benefit (verifiers and pipeline filters consult the store
 * directly via FactEnvelopeStore.get / getActive).
 */

export interface FactProvenance {
  toolName: string;
  args: unknown;
  durationMs: number;
  /** Optional: which verifier confirmed this fact (e.g. "GoalVerifier"). */
  verifiedBy?: string;
  /** Optional: 0..1 confidence reported by the producing tool. */
  confidence?: number;
}

export interface FactEnvelope {
  content: unknown;
  provenance: FactProvenance;
  retracted: boolean;
}

export interface FactEnvelopeStoreOptions {
  /**
   * Soft cap on entries kept per session. When exceeded the oldest
   * (lowest turnIndex) entry is evicted FIFO. Default 200 — enough for
   * any realistic single-session conversation; prevents unbounded growth
   * over a long-running daemon.
   */
  maxPerSession?: number;
}

const DEFAULT_MAX_PER_SESSION = 200;

export class FactEnvelopeStore {
  // Outer key = sessionId. Inner Map preserves insertion order, which lets
  // us evict the oldest entry by deleting the first key.
  private readonly bySession = new Map<string, Map<number, FactEnvelope>>();
  private readonly maxPerSession: number;

  constructor(opts: FactEnvelopeStoreOptions = {}) {
    this.maxPerSession = opts.maxPerSession ?? DEFAULT_MAX_PER_SESSION;
  }

  record(
    sessionId: string,
    turnIndex: number,
    envelope: Omit<FactEnvelope, "retracted">,
  ): void {
    let session = this.bySession.get(sessionId);
    if (!session) {
      session = new Map();
      this.bySession.set(sessionId, session);
    }
    session.set(turnIndex, { ...envelope, retracted: false });

    if (session.size > this.maxPerSession) {
      const oldestKey = session.keys().next().value;
      if (oldestKey !== undefined) session.delete(oldestKey);
    }
  }

  get(sessionId: string, turnIndex: number): FactEnvelope | null {
    return this.bySession.get(sessionId)?.get(turnIndex) ?? null;
  }

  /**
   * Mark an envelope as retracted in place. Returns the updated envelope,
   * or null when the (sessionId, turnIndex) pair has no entry. T17 calls
   * this from the verifier path and emits `fact:retracted` so the pipeline
   * can drop it on the next turn.
   */
  retract(sessionId: string, turnIndex: number): FactEnvelope | null {
    const entry = this.bySession.get(sessionId)?.get(turnIndex);
    if (!entry) return null;
    entry.retracted = true;
    return entry;
  }

  /** Return all non-retracted envelopes for a session, ordered by turnIndex. */
  getActive(sessionId: string): FactEnvelope[] {
    const session = this.bySession.get(sessionId);
    if (!session) return [];
    const out: FactEnvelope[] = [];
    for (const env of session.values()) {
      if (!env.retracted) out.push(env);
    }
    return out;
  }

  clearSession(sessionId: string): void {
    this.bySession.delete(sessionId);
  }
}
