/**
 * Keymap constants for TUI v2.
 *
 * Ink's useInput hook delivers key objects; these are the semantic bindings.
 * Actual handling happens in Composer and screen-level components.
 */

export const KEYBINDINGS = {
  /** Submit message. */
  SEND: "return",
  /** Insert newline in composer (Shift+Enter / Ctrl+J). */
  NEWLINE_SHIFT: "shift+return",
  NEWLINE_CTRL: "ctrl+j",
  /** Interrupt generation (two consecutive Escapes). */
  INTERRUPT: "escape",
  /** Reverse history search. */
  HISTORY_SEARCH: "ctrl+r",
  /** Toggle reasoning overlay. */
  REASONING_OVERLAY: "ctrl+t",
  /** Open parliament theater. */
  PARLIAMENT: "ctrl+p",
  /** Capture knowledge pellet. */
  SAVE_PELLET: "ctrl+s",
  /** Shortcut help overlay. */
  HELP: "?",
  /** Command palette. */
  PALETTE: "ctrl+k",
  /** History previous. */
  HISTORY_PREV: "up",
  /** History next. */
  HISTORY_NEXT: "down",
} as const;
