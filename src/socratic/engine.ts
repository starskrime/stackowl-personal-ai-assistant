/**
 * StackOwl — Socratic Engine
 *
 * Per-session toggle that makes the owl respond only with probing questions.
 * No persistence needed — purely a system prompt directive.
 */

import type { SocraticSubMode, SocraticSession } from './types.js';

const MODE_DIRECTIVES: Record<SocraticSubMode, string> = {
  pure:
    'You are in Socratic Mode (pure). NEVER give answers, explanations, or solutions. ' +
    'Respond ONLY with questions that help the user discover the answer themselves. ' +
    'Ask one incisive question at a time. Build on their responses to go deeper.',

  guided:
    'You are in Socratic Mode (guided). Lead the user toward understanding through questions, ' +
    'but you may give brief hints or confirmations. Each response should contain 1-2 questions ' +
    'and at most one guiding sentence.',

  reflective:
    'You are in Socratic Mode (reflective). Help the user reflect on their own thinking. ' +
    'Mirror their statements back as questions. Ask "why" and "what if" variations. ' +
    'Help them see assumptions they\'re making.',

  devils_advocate:
    'You are in Socratic Mode (devil\'s advocate). Challenge EVERY position the user takes. ' +
    'Find the strongest counter-argument to whatever they say. Be intellectually rigorous ' +
    'but not hostile. Push them to steel-man their own views.',
};

export class SocraticEngine {
  private activeSessions: Map<string, SocraticSession> = new Map();

  /**
   * Activate Socratic mode for a session.
   */
  activate(sessionId: string, mode: SocraticSubMode = 'guided'): SocraticSession {
    const session: SocraticSession = {
      sessionId,
      mode,
      exchangeCount: 0,
      activatedAt: new Date().toISOString(),
      insights: [],
    };
    this.activeSessions.set(sessionId, session);
    return session;
  }

  /**
   * Deactivate Socratic mode for a session.
   */
  deactivate(sessionId: string): SocraticSession | null {
    const session = this.activeSessions.get(sessionId);
    this.activeSessions.delete(sessionId);
    return session ?? null;
  }

  /**
   * Check if a session is in Socratic mode.
   */
  isActive(sessionId: string): boolean {
    return this.activeSessions.has(sessionId);
  }

  /**
   * Get the active session info.
   */
  getSession(sessionId: string): SocraticSession | null {
    return this.activeSessions.get(sessionId) ?? null;
  }

  /**
   * Record an exchange and return updated session.
   */
  recordExchange(sessionId: string): SocraticSession | null {
    const session = this.activeSessions.get(sessionId);
    if (!session) return null;
    session.exchangeCount++;
    return session;
  }

  /**
   * Get the system prompt directive for the current session.
   * Returns empty string if Socratic mode is not active.
   */
  toContextString(sessionId: string): string {
    const session = this.activeSessions.get(sessionId);
    if (!session) return '';

    const directive = MODE_DIRECTIVES[session.mode];
    return (
      '\n<socratic_mode>\n' +
      directive + '\n' +
      `Exchange ${session.exchangeCount + 1}. ` +
      (session.exchangeCount >= 8
        ? 'Consider summarizing key insights discovered so far before continuing.'
        : '') +
      '\n</socratic_mode>\n'
    );
  }

  /**
   * Change the sub-mode of an active session.
   */
  changeMode(sessionId: string, mode: SocraticSubMode): SocraticSession | null {
    const session = this.activeSessions.get(sessionId);
    if (!session) return null;
    session.mode = mode;
    return session;
  }

  /**
   * Format status for display.
   */
  formatStatus(sessionId: string): string {
    const session = this.activeSessions.get(sessionId);
    if (!session) return 'Socratic mode is **off**.';

    return (
      `Socratic mode is **on** (${session.mode})\n` +
      `Exchanges so far: ${session.exchangeCount}\n` +
      `Active since: ${new Date(session.activatedAt).toLocaleString()}`
    );
  }
}
