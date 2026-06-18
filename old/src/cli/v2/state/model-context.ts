// Context window sizes (tokens) for known models.
// Used to compute ctx% in the StatusBar.
export const MODEL_CONTEXT_WINDOWS: Record<string, number> = {
  "claude-opus-4-7":              200_000,
  "claude-sonnet-4-6":            200_000,
  "claude-haiku-4-5":             200_000,
  "claude-haiku-4-5-20251001":    200_000,
  "claude-3-5-sonnet-20241022":   200_000,
  "claude-3-5-haiku-20241022":    200_000,
  "claude-3-opus-20240229":       200_000,
  "gpt-4o":                       128_000,
  "gpt-4o-mini":                  128_000,
  "gpt-4-turbo":                  128_000,
  "o1":                           200_000,
  "o3":                           200_000,
  "o3-mini":                      200_000,
  "gemini-2.0-flash":           1_000_000,
  "gemini-2.5-pro":               200_000,
};

/** Returns context window size or undefined for unknown models. */
export function getContextWindow(model: string): number | undefined {
  // Exact match first; then prefix match for versioned names
  if (MODEL_CONTEXT_WINDOWS[model]) return MODEL_CONTEXT_WINDOWS[model];
  const key = Object.keys(MODEL_CONTEXT_WINDOWS).find(
    (k) => model.startsWith(k) || k.startsWith(model),
  );
  return key ? MODEL_CONTEXT_WINDOWS[key] : undefined;
}
