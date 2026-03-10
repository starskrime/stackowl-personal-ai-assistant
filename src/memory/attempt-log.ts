/**
 * StackOwl — Session Attempt Log
 *
 * Tracks every tool call + outcome across ALL turns in a session.
 * Injected into the system prompt on every new request so the model
 * can see what was already tried and what failed — preventing it from
 * repeating approaches that didn't work in previous messages.
 *
 * This is the primary fix for "assistant repeats itself across turns":
 * the raw session history gets compressed/trimmed and loses failure context,
 * but this log is always injected fresh and is never compressed away.
 */

export type AttemptOutcome = 'success' | 'soft-fail' | 'hard-fail' | 'duplicate-blocked';

export interface Attempt {
  /** Which user turn this happened in (1 = first message, 2 = second, etc.) */
  turn: number;
  toolName: string;
  /** Key args that identify WHAT was attempted (not full args — kept short) */
  argsSummary: string;
  outcome: AttemptOutcome;
  /** First 180 chars of result — enough to understand why it failed */
  resultSummary: string;
}

const MAX_ATTEMPTS = 40;   // cap so injection doesn't grow unboundedly
const MAX_RESULT_CHARS = 180;
const MAX_ARGS_CHARS = 80;

export class AttemptLog {
  private attempts: Attempt[] = [];
  private currentTurn = 0;

  /** Call this at the start of each new user message */
  newTurn(): void {
    this.currentTurn++;
  }

  /** Record a tool call and its outcome */
  record(
    toolName: string,
    args: Record<string, unknown>,
    outcome: AttemptOutcome,
    result: string,
  ): void {
    // Summarise args: prefer 'command', 'url', 'query', 'path' fields which are most meaningful
    const argValue =
      (args['command'] ?? args['url'] ?? args['query'] ?? args['path'] ??
       args['prompt'] ?? args['text'] ?? JSON.stringify(args)) as string;
    const argsSummary = String(argValue).slice(0, MAX_ARGS_CHARS);

    // Strip control markers from result summary so they don't confuse the model
    const cleaned = result
      .replace(/\[SYSTEM[^\]]*\]/g, '')
      .replace(/EXIT_CODE:\s*\d+/g, (m) => m.trim())
      .trim();
    const resultSummary = cleaned.slice(0, MAX_RESULT_CHARS);

    this.attempts.push({
      turn: this.currentTurn,
      toolName,
      argsSummary,
      outcome,
      resultSummary,
    });

    // Keep bounded — drop oldest attempts when full
    if (this.attempts.length > MAX_ATTEMPTS) {
      this.attempts.shift();
    }
  }

  /** Returns true if there are any recorded attempts */
  hasAttempts(): boolean {
    return this.attempts.length > 0;
  }

  /**
   * Format the attempt log as a concise system prompt block.
   * Groups by turn so the model can see the progression clearly.
   *
   * Example output:
   *   Turn 1: run_shell_command("curl https://...") → FAILED (curl: not found in sandbox)
   *   Turn 1: web_crawl("https://...") → SUCCESS
   *   Turn 2: run_shell_command("python3 script.py") → FAILED (python3 not in Alpine)
   */
  toPromptBlock(): string {
    if (this.attempts.length === 0) return '';

    const OUTCOME_LABEL: Record<AttemptOutcome, string> = {
      'success':           '✓ SUCCESS',
      'soft-fail':         '✗ FAILED',
      'hard-fail':         '✗ ERROR',
      'duplicate-blocked': '⊘ SKIPPED (already tried)',
    };

    const lines = this.attempts.map(a => {
      const label = OUTCOME_LABEL[a.outcome];
      const detail = a.outcome !== 'success' ? ` — ${a.resultSummary}` : '';
      return `  Turn ${a.turn}: ${a.toolName}("${a.argsSummary}") → ${label}${detail}`;
    });

    return (
      `## What Has Been Tried This Session\n` +
      `The following tool calls were made in earlier turns. ` +
      `Before planning your next action, read this list carefully — ` +
      `do NOT repeat any approach marked FAILED or SKIPPED.\n\n` +
      lines.join('\n') +
      `\n\nIf every reasonable approach has been tried and failed, say so directly and ask the user for guidance.`
    );
  }
}

/**
 * Gateway-level registry: one AttemptLog per active session.
 * Stored outside the engine so it survives across multiple handle() calls
 * for the same session (i.e., across user messages).
 */
export class AttemptLogRegistry {
  private logs: Map<string, AttemptLog> = new Map();

  get(sessionId: string): AttemptLog {
    if (!this.logs.has(sessionId)) {
      this.logs.set(sessionId, new AttemptLog());
    }
    return this.logs.get(sessionId)!;
  }

  delete(sessionId: string): void {
    this.logs.delete(sessionId);
  }

  /** Prune logs for sessions not seen in the last 2 hours (matches session timeout) */
  pruneStale(activeSessionIds: Set<string>): void {
    for (const id of this.logs.keys()) {
      if (!activeSessionIds.has(id)) {
        this.logs.delete(id);
      }
    }
  }
}
