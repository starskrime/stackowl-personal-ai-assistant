/**
 * Pure schema utilities for /config command.
 * No gateway/bridge dependencies — unit-testable in isolation.
 */

// ─── Type inference ───────────────────────────────────────────────────────────

export type ConfigValueType =
  | "string"
  | "number"
  | "boolean"
  | "object"
  | "array"
  | "null";

export function inferType(value: unknown): ConfigValueType {
  if (value === null || value === undefined) return "null";
  if (Array.isArray(value)) return "array";
  return typeof value as ConfigValueType;
}

// ─── Path traversal ──────────────────────────────────────────────────────────

export function getAtPath(obj: unknown, dotPath: string): unknown {
  if (!dotPath) return obj;
  const parts = dotPath.split(".");
  let cur: unknown = obj;
  for (const part of parts) {
    if (cur === null || cur === undefined || typeof cur !== "object") return undefined;
    cur = (cur as Record<string, unknown>)[part];
  }
  return cur;
}

export function setAtPath(obj: unknown, dotPath: string, value: unknown): void {
  const parts = dotPath.split(".");
  let cur: Record<string, unknown> = obj as Record<string, unknown>;
  for (let i = 0; i < parts.length - 1; i++) {
    const key = parts[i]!;
    if (cur[key] === null || cur[key] === undefined || typeof cur[key] !== "object") {
      cur[key] = {};
    }
    cur = cur[key] as Record<string, unknown>;
  }
  cur[parts[parts.length - 1]!] = value;
}

// ─── Secret detection + masking ──────────────────────────────────────────────

const SENSITIVE_PATHS: RegExp[] = [
  /^providers\.[^.]+\.apiKey$/,
  /^camofox\.apiKey$/,
  /^telegram\.botToken$/,
  /^slack\.(botToken|appToken|signingSecret)$/,
  /^mcp\.servers\.\d+\.env\..+$/,
];

export function isSecretPath(dotPath: string): boolean {
  return SENSITIVE_PATHS.some((re) => re.test(dotPath));
}

export function maskSecret(v: string | null | undefined): string {
  if (!v) return "<unset>";
  if (v.length <= 4) return "•".repeat(v.length);
  return "…" + v.slice(-4);
}

export function maskIfSecret(dotPath: string, value: unknown): string {
  if (isSecretPath(dotPath) && typeof value === "string") {
    return maskSecret(value);
  }
  return displayValue(value);
}

// ─── Display helpers ─────────────────────────────────────────────────────────

export function displayValue(value: unknown): string {
  if (value === null || value === undefined) return "<unset>";
  if (Array.isArray(value)) return `[${value.length}]`;
  if (typeof value === "object") return "{…}";
  if (typeof value === "boolean") return String(value);
  if (typeof value === "number") return String(value);
  if (typeof value === "string") {
    // Truncate long strings for panel display
    return value.length > 48 ? value.slice(0, 45) + "…" : value;
  }
  return String(value);
}

// ─── Input parsing ───────────────────────────────────────────────────────────

export type ParseResult =
  | { ok: true; value: unknown }
  | { ok: false; error: string };

export function parseScalarInput(raw: string, targetType: ConfigValueType): ParseResult {
  if (raw.trim() === "") return { ok: false, error: "value cannot be empty" };

  switch (targetType) {
    case "number": {
      const n = Number(raw.trim());
      if (isNaN(n)) return { ok: false, error: `"${raw}" is not a number` };
      return { ok: true, value: n };
    }
    case "boolean": {
      const lower = raw.trim().toLowerCase();
      if (lower === "true" || lower === "1" || lower === "yes") return { ok: true, value: true };
      if (lower === "false" || lower === "0" || lower === "no") return { ok: true, value: false };
      return { ok: false, error: `"${raw}" is not a boolean (use true/false)` };
    }
    case "null":
    case "string":
    default:
      return { ok: true, value: raw };
  }
}
