/**
 * StackOwl — Web Tool Envelope
 *
 * Single source of truth for the WebToolResult contract every web tool returns.
 * The registry parses the JSON; non-web tools are unaffected.
 */

export type WebToolErrorCode =
  | "BLOCKED_BY_ANTI_BOT"
  | "PAYWALL"
  | "RATE_LIMITED"
  | "TIMEOUT"
  | "NOT_FOUND"
  | "INVALID_URL"
  | "ALL_TIERS_UNAVAILABLE"
  | "INTERNAL_ERROR";

export type TierName = "http" | "camofox" | "scrapling";

export type TierOutcome =
  | "success"
  | "blocked"
  | "timeout"
  | "unavailable"
  | "error"
  | "skipped-by-hint";

export type BlockedReason =
  | "cloudflare"
  | "captcha"
  | "paywall"
  | "rate-limit"
  | "access-denied"
  | "other";

export interface TierAttempt {
  tier: number;
  name: TierName;
  durationMs: number;
  outcome: TierOutcome;
  blockedReason?: BlockedReason;
  httpStatus?: number;
}

export interface WebToolError {
  code: WebToolErrorCode;
  message: string;
  attemptedTiers: TierAttempt[];
  suggestedEscalation?: string;
}

export type WebToolData =
  | { kind: "page"; url: string; title?: string; content: string; contentType?: string }
  | { kind: "search"; query: string; results: Array<{ title: string; url: string; snippet?: string }> };

export type WebToolResult =
  | { success: true; data: WebToolData }
  | { success: false; error: WebToolError };

const ERROR_CODES: ReadonlySet<string> = new Set<WebToolErrorCode>([
  "BLOCKED_BY_ANTI_BOT",
  "PAYWALL",
  "RATE_LIMITED",
  "TIMEOUT",
  "NOT_FOUND",
  "INVALID_URL",
  "ALL_TIERS_UNAVAILABLE",
  "INTERNAL_ERROR",
]);

const NAMES: ReadonlySet<TierName> = new Set<TierName>(["http", "camofox", "scrapling"]);
const OUTCOMES: ReadonlySet<TierOutcome> = new Set<TierOutcome>([
  "success", "blocked", "timeout", "unavailable", "error", "skipped-by-hint",
]);

const ALIAS_CODES = new Set<WebToolErrorCode>(["BLOCKED_BY_ANTI_BOT", "ALL_TIERS_UNAVAILABLE"]);

export function serializeWebToolResult(result: WebToolResult): string {
  if (!result.success) {
    const needsAlias = ALIAS_CODES.has(result.error.code) && !result.error.message.startsWith("BLOCKED:");
    if (needsAlias) {
      return JSON.stringify({
        success: false,
        error: { ...result.error, message: `BLOCKED: ${result.error.message}` },
      });
    }
  }
  return JSON.stringify(result);
}

export function parseWebToolResult(s: string): WebToolResult | null {
  let parsed: unknown;
  try { parsed = JSON.parse(s); } catch { return null; }
  if (!isWebToolResult(parsed)) return null;
  return parsed;
}

export function isWebToolResult(v: unknown): v is WebToolResult {
  if (!v || typeof v !== "object") return false;
  const o = v as Record<string, unknown>;
  if (o.success === true) {
    const d = o.data as Record<string, unknown> | undefined;
    if (!d || typeof d !== "object") return false;
    if (d.kind === "page") return typeof d.url === "string" && typeof d.content === "string";
    if (d.kind === "search") return typeof d.query === "string" && Array.isArray(d.results);
    return false;
  }
  if (o.success === false) return isWebToolError(o.error);
  return false;
}

export function isWebToolError(v: unknown): v is WebToolError {
  if (!v || typeof v !== "object") return false;
  const e = v as Record<string, unknown>;
  if (typeof e.code !== "string" || !ERROR_CODES.has(e.code)) return false;
  if (typeof e.message !== "string") return false;
  if (!Array.isArray(e.attemptedTiers)) return false;
  for (const t of e.attemptedTiers) {
    if (!t || typeof t !== "object") return false;
    const tt = t as Record<string, unknown>;
    if (typeof tt.tier !== "number") return false;
    if (typeof tt.name !== "string" || !NAMES.has(tt.name as TierName)) return false;
    if (typeof tt.outcome !== "string" || !OUTCOMES.has(tt.outcome as TierOutcome)) return false;
    if (typeof tt.durationMs !== "number") return false;
  }
  return true;
}

export function buildAttemptSummaryXml(result: WebToolResult): string {
  if (result.success) return "";
  const lines: string[] = [];
  lines.push(`<tool_attempt_summary code="${escapeAttr(result.error.code)}">`);
  for (const t of result.error.attemptedTiers) {
    const reason = t.blockedReason ? ` reason="${escapeAttr(t.blockedReason)}"` : "";
    const status = t.httpStatus !== undefined ? ` httpStatus="${t.httpStatus}"` : "";
    lines.push(
      `  <tier n="${t.tier}" name="${escapeAttr(t.name)}" outcome="${escapeAttr(t.outcome)}"${reason}${status} durationMs="${t.durationMs}"/>`,
    );
  }
  if (result.error.suggestedEscalation) {
    lines.push(`  <suggestion>${escapeText(result.error.suggestedEscalation)}</suggestion>`);
  }
  lines.push(`</tool_attempt_summary>`);
  return lines.join("\n");
}

function escapeAttr(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}
function escapeText(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
