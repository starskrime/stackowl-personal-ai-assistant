/** Shared spinner constants for all TUI v2 animated components. */
import { colors } from "../theme/tokens.js";

export const STACKOWL_SPINNER = ["·", "◌", "◍", "◉", "✳", "✶"] as const;
export const SPINNER_AMBER = colors.brand;  // #F5A623 — sourced from design token
export const SPINNER_INTERVAL_MS = 80;
