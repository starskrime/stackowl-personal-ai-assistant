/**
 * StackOwl Observability — PII / secret redaction
 *
 * Applied before any log record reaches a sink.
 * Only operates on the string fields that could carry secrets.
 */

export type RedactTarget = "tokens" | "emails" | "paths";

const TOKEN_PATTERNS: RegExp[] = [
  /Bearer\s+[A-Za-z0-9._-]{20,}/g,
  /sk-[A-Za-z0-9]{20,}/g,
  /sk-ant-[A-Za-z0-9._-]{20,}/g,
  /xoxb-[A-Za-z0-9-]{20,}/g,
  /xoxp-[A-Za-z0-9-]{20,}/g,
  /(?:OPENAI|ANTHROPIC|GROQ|MISTRAL|GEMINI|HUGGINGFACE)_API_KEY\s*[:=]\s*\S+/gi,
  /ghp_[A-Za-z0-9]{36}/g,
  /github_pat_[A-Za-z0-9_]{82}/g,
];

const EMAIL_PATTERN = /[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}/g;

function redactString(s: string, targets: Set<RedactTarget>): string {
  if (targets.has("tokens")) {
    for (const rx of TOKEN_PATTERNS) {
      s = s.replace(rx, "<redacted:token>");
    }
  }
  if (targets.has("emails")) {
    s = s.replace(EMAIL_PATTERN, "<redacted:email>");
  }
  return s;
}

/**
 * Walk a log record's string-valued fields and apply redaction in-place.
 * Operates only on `msg`, `err.message`, `err.stack`, and `fields.*`.
 */
export function redactRecord(
  record: Record<string, unknown>,
  targets: RedactTarget[],
): void {
  if (targets.length === 0) return;
  const t = new Set(targets);

  if (typeof record.msg === "string") {
    record.msg = redactString(record.msg, t);
  }

  if (record.err && typeof record.err === "object") {
    const err = record.err as Record<string, unknown>;
    if (typeof err.message === "string") err.message = redactString(err.message, t);
    if (typeof err.stack   === "string") err.stack   = redactString(err.stack,   t);
  }

  if (record.fields && typeof record.fields === "object") {
    for (const [k, v] of Object.entries(record.fields as Record<string, unknown>)) {
      if (typeof v === "string") {
        (record.fields as Record<string, unknown>)[k] = redactString(v, t);
      }
    }
  }
}
