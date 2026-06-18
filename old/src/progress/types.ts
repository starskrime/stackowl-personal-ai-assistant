/**
 * ProgressNotifier — the interface every channel implements.
 *
 * The rendering strategy (spinner, typing indicator, ACK message, etc.)
 * is entirely the channel's concern. The contract is just these three
 * lifecycle methods, all scoped by turnId.
 */
export interface ProgressNotifier {
  /**
   * Called once when a turn begins.
   * phrase is a random "Working on it…" string from pickRandomPhrase().
   */
  start(phrase: string, turnId: string): Promise<void>;

  /**
   * Called when a tool starts executing.
   * text is a short human-readable status from getToolStatusPhrase().
   */
  update(text: string, turnId: string): Promise<void>;

  /**
   * Called when the turn is fully complete and the final answer has been sent.
   */
  stop(turnId: string): Promise<void>;
}
