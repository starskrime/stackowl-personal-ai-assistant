/**
 * StackOwl Observability — Log analyzer
 *
 * Pure function: takes a window of LogRecord[] and returns a LogSummary
 * describing hotspots the CognitiveLoop can act on.
 *
 * Signals extracted:
 *   errorsByModule     — which modules are producing the most errors
 *   slowSpans          — operations whose p95 latency is high
 *   repeatFailures     — same error recurring >= 3 times (deduplicated by normalized msg)
 *   emptyResultTools   — tools returning empty results frequently
 *   exhaustionMarkers  — ReAct loops that hit the MAX_TOOL_ITERATIONS cap
 *   capabilityGaps     — phrases like "no tool for X" / "I cannot do Y" near user turns
 */

import type { LogRecord } from "./schema.js";

export interface ErrorsByModule {
  module: string;
  count: number;
  sampleMsgs: string[];
}

export interface SlowSpan {
  spanName: string;
  p95Ms: number;
  samples: number;
  sampleTraces: string[];
}

export interface RepeatFailure {
  normalizedMsg: string;
  count: number;
  module: string;
  spanName?: string;
}

export interface EmptyResultTool {
  tool: string;
  count: number;
}

export interface ExhaustionMarker {
  traceId: string;
  iterations: number;
  module: string;
}

export interface CapabilityGap {
  phrase: string;
  supportingTraces: string[];
}

export interface LogSummary {
  windowMinutes: number;
  totalRecords: number;
  errorsByModule: ErrorsByModule[];
  slowSpans: SlowSpan[];
  repeatFailures: RepeatFailure[];
  emptyResultTools: EmptyResultTool[];
  exhaustionMarkers: ExhaustionMarker[];
  capabilityGaps: CapabilityGap[];
}

// ── Capability gap patterns ───────────────────────────────────────

const GAP_PATTERNS: RegExp[] = [
  /no\s+tool\s+(?:available\s+)?for\s+(.+)/i,
  /i\s+(?:can|cannot|can't)\s+(?:not\s+)?\b(send|read|fetch|access|connect|write|delete|upload|download)\b[^.]+/i,
  /don't\s+have\s+(?:the\s+)?(?:ability|capability|access)\s+to\s+(.+)/i,
  /unable\s+to\s+(.{10,60})/i,
];

function extractGapPhrase(msg: string): string | null {
  for (const rx of GAP_PATTERNS) {
    const m = rx.exec(msg);
    if (m) return m[0].trim().slice(0, 120);
  }
  return null;
}

// ── Normalization ─────────────────────────────────────────────────

/** Strip UUIDs, timestamps, and numbers so we can deduplicate similar messages. */
function normalizeMsg(msg: string): string {
  return msg
    .replace(/[0-9a-f]{8}-[0-9a-f-]{23,35}/gi, "<id>")  // UUIDs
    .replace(/\d{13,}/g, "<ts>")                          // epoch ms
    .replace(/\d+/g, "<n>")                               // other numbers
    .toLowerCase()
    .slice(0, 120);
}

// ── Percentile helper ─────────────────────────────────────────────

function p95(values: number[]): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const idx = Math.floor(sorted.length * 0.95);
  return sorted[Math.min(idx, sorted.length - 1)];
}

// ── Main ──────────────────────────────────────────────────────────

export function summarize(records: LogRecord[]): LogSummary {
  if (records.length === 0) {
    return {
      windowMinutes: 0,
      totalRecords: 0,
      errorsByModule: [],
      slowSpans: [],
      repeatFailures: [],
      emptyResultTools: [],
      exhaustionMarkers: [],
      capabilityGaps: [],
    };
  }

  // Window size
  const times = records.map((r) => new Date(r.ts).getTime()).filter((t) => !isNaN(t));
  const windowMinutes = times.length >= 2
    ? Math.round((Math.max(...times) - Math.min(...times)) / 60_000)
    : 0;

  // ── errorsByModule ────────────────────────────────────────────
  const errorMap = new Map<string, string[]>();
  for (const r of records) {
    if (r.level !== "error" && r.level !== "fatal") continue;
    const bucket = errorMap.get(r.module) ?? [];
    bucket.push(r.msg);
    errorMap.set(r.module, bucket);
  }
  const errorsByModule: ErrorsByModule[] = [...errorMap.entries()]
    .sort((a, b) => b[1].length - a[1].length)
    .slice(0, 10)
    .map(([module, msgs]) => ({
      module,
      count: msgs.length,
      sampleMsgs: [...new Set(msgs)].slice(0, 3),
    }));

  // ── slowSpans ─────────────────────────────────────────────────
  const spanDurations = new Map<string, { durations: number[]; traces: string[] }>();
  for (const r of records) {
    if (r.durationMs === undefined || !r.spanName) continue;
    const key = r.spanName;
    const bucket = spanDurations.get(key) ?? { durations: [], traces: [] };
    bucket.durations.push(r.durationMs);
    if (r.traceId && bucket.traces.length < 5 && !bucket.traces.includes(r.traceId)) {
      bucket.traces.push(r.traceId);
    }
    spanDurations.set(key, bucket);
  }
  const slowSpans: SlowSpan[] = [...spanDurations.entries()]
    .map(([spanName, { durations, traces }]) => ({
      spanName,
      p95Ms: p95(durations),
      samples: durations.length,
      sampleTraces: traces,
    }))
    .filter((s) => s.p95Ms > 500)  // only spans over 500ms p95 are interesting
    .sort((a, b) => b.p95Ms - a.p95Ms)
    .slice(0, 10);

  // ── repeatFailures ────────────────────────────────────────────
  const failureMap = new Map<string, { count: number; module: string; spanName?: string }>();
  for (const r of records) {
    if (r.level !== "error" && r.level !== "warn") continue;
    const key = `${r.module}::${normalizeMsg(r.msg)}`;
    const existing = failureMap.get(key);
    if (existing) {
      existing.count++;
    } else {
      failureMap.set(key, { count: 1, module: r.module, spanName: r.spanName });
    }
  }
  const repeatFailures: RepeatFailure[] = [...failureMap.entries()]
    .filter(([, v]) => v.count >= 3)
    .sort(([, a], [, b]) => b.count - a.count)
    .slice(0, 10)
    .map(([k, v]) => ({
      normalizedMsg: k.split("::")[1],
      count: v.count,
      module: v.module,
      spanName: v.spanName,
    }));

  // ── emptyResultTools ──────────────────────────────────────────
  const emptyToolMap = new Map<string, number>();
  for (const r of records) {
    if (r.fields?.resultEmpty !== true) continue;
    const tool = String(r.fields.tool ?? r.module);
    emptyToolMap.set(tool, (emptyToolMap.get(tool) ?? 0) + 1);
  }
  const emptyResultTools: EmptyResultTool[] = [...emptyToolMap.entries()]
    .sort(([, a], [, b]) => b - a)
    .slice(0, 10)
    .map(([tool, count]) => ({ tool, count }));

  // ── exhaustionMarkers ─────────────────────────────────────────
  const exhaustionMarkers: ExhaustionMarker[] = records
    .filter((r) => r.fields?.exhaustion === true)
    .map((r) => ({
      traceId: r.traceId ?? "unknown",
      iterations: Number(r.fields?.iterations ?? 0),
      module: r.module,
    }))
    .slice(0, 20);

  // ── capabilityGaps ────────────────────────────────────────────
  const gapMap = new Map<string, Set<string>>();
  for (const r of records) {
    const phrase = extractGapPhrase(r.msg);
    if (!phrase) continue;
    const normalized = normalizeMsg(phrase);
    const traces = gapMap.get(normalized) ?? new Set<string>();
    if (r.traceId) traces.add(r.traceId);
    gapMap.set(normalized, traces);
  }
  const capabilityGaps: CapabilityGap[] = [...gapMap.entries()]
    .sort(([, a], [, b]) => b.size - a.size)
    .slice(0, 10)
    .map(([phrase, traces]) => ({
      phrase,
      supportingTraces: [...traces].slice(0, 5),
    }));

  return {
    windowMinutes,
    totalRecords: records.length,
    errorsByModule,
    slowSpans,
    repeatFailures,
    emptyResultTools,
    exhaustionMarkers,
    capabilityGaps,
  };
}
