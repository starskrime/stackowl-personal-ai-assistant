/** TUI v2 spinner constants. Shared data re-exported from src/shared/progress.ts. */
import { colors } from "../theme/tokens.js";

// Re-export shared progress data so existing imports continue to work unchanged.
export {
  THINKING_MESSAGES,
  STACKOWL_SPINNER,
  FADE_COLORS,
  LANG_INTERVAL_MS,
  pickRandomPhrase,
  TOOL_STATUS_PHRASES,
  getToolStatusPhrase,
} from "../../../shared/progress.js";

/** Spinner icon color — sourced from the brand design token. */
export const SPINNER_AMBER = colors.brand;
/** Raw spinner frame interval (ms). */
export const SPINNER_INTERVAL_MS = 80;
/** Slower interval for tool call cards (ms). */
export const TOOL_SPIN_INTERVAL_MS = 150;
/** Interval for the thinking indicator spinner (ms). */
export const THINKING_SPIN_INTERVAL_MS = 250;
