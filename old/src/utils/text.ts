/**
 * StackOwl — Shared Utilities
 *
 * Common utility functions used across modules.
 */

const STOP_WORDS = new Set([
  "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
  "have", "has", "had", "do", "does", "did", "will", "would", "could",
  "should", "may", "might", "must", "shall", "can", "need", "dare",
  "ought", "used", "to", "of", "in", "for", "on", "with", "at", "by",
  "from", "as", "into", "through", "during", "before", "after",
  "above", "below", "between", "under", "again", "further", "then",
  "once", "here", "there", "when", "where", "why", "how", "all",
  "each", "few", "more", "most", "other", "some", "such", "no", "nor",
  "not", "only", "own", "same", "so", "than", "too", "very", "just",
  "and", "but", "or", "if", "because", "until", "while", "this",
  "that", "these", "those", "i", "me", "my", "myself", "we", "our",
  "you", "your", "he", "him", "his", "she", "her", "it", "its",
  "they", "them", "their", "what", "which", "who", "whom", "any",
  "both", "want", "like", "help", "know", "think",
]);

/**
 * Extract meaningful keywords from a description text.
 * Filters out stop words and returns unique keywords.
 */
export function extractKeywords(text: string, maxKeywords = 8): string[] {
  const words = text.toLowerCase()
    .replace(/[^a-z0-9\s]/g, " ")
    .split(/\s+/)
    .filter(w => w.length >= 3 && !STOP_WORDS.has(w));
  return [...new Set(words)].slice(0, maxKeywords);
}

/**
 * Generate a unique owl name that doesn't conflict with existing names.
 */
export function generateUniqueName(
  baseName: string,
  existingNames: string[],
  maxAttempts = 100,
): string {
  const sanitized = baseName.replace(/[^a-zA-Z0-9]/g, "") || "CustomOwl";
  const base = sanitized.charAt(0).toUpperCase() + sanitized.slice(1).toLowerCase();

  const existingLower = new Set(existingNames.map(n => n.toLowerCase()));

  if (!existingLower.has(base.toLowerCase())) {
    return base;
  }

  for (let i = 1; i <= maxAttempts; i++) {
    const candidate = base + i;
    if (!existingLower.has(candidate.toLowerCase())) {
      return candidate;
    }
  }

  return `CustomOwl_${Date.now()}`;
}
