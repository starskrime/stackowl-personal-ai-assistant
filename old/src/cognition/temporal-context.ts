/**
 * StackOwl — Temporal Context
 *
 * Computes time-awareness signals and formats them for system prompt injection.
 * Zero LLM cost — pure date/time arithmetic.
 *
 * Gives the owl a sense of:
 *   - What time/day it is right now
 *   - How long ago the user was last here
 *   - Whether this is a returning user after a gap
 *   - What the previous session was about
 */

import type { Session } from "../memory/store.js";
import type { SessionStore } from "../memory/store.js";

export interface TemporalSnapshot {
  now: Date;
  timezone: string;
  dayOfWeek: string;
  timeOfDay: "morning" | "afternoon" | "evening" | "night";
  dayContext: string;

  /** How long since the first message in the current session */
  sessionAge: string | null;
  /** How long since the last user message in the current session */
  lastMessageGap: string | null;
  /** How long since the previous session ended */
  lastSessionGap: string | null;
  /** Brief topic of the previous session (keyword-extracted, no LLM) */
  lastSessionTopic: string | null;
  /** True if gap > 4 hours — owl should acknowledge the return */
  isReturningUser: boolean;
}

const DAYS = [
  "Sunday",
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
];

function getTimeOfDay(hour: number): TemporalSnapshot["timeOfDay"] {
  if (hour >= 5 && hour < 12) return "morning";
  if (hour >= 12 && hour < 17) return "afternoon";
  if (hour >= 17 && hour < 21) return "evening";
  return "night";
}

function isWeekend(day: number): boolean {
  return day === 0 || day === 6;
}

function formatDuration(ms: number): string {
  const seconds = Math.floor(ms / 1000);
  if (seconds < 60) return `${seconds} seconds`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"}`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    const remMin = minutes % 60;
    return remMin > 0
      ? `${hours} hour${hours === 1 ? "" : "s"} ${remMin} min`
      : `${hours} hour${hours === 1 ? "" : "s"}`;
  }
  const days = Math.floor(hours / 24);
  return `${days} day${days === 1 ? "" : "s"}`;
}

function formatRelativeTime(date: Date, now: Date, timezone: string): string {
  const diffMs = now.getTime() - date.getTime();
  const diffHours = diffMs / (1000 * 60 * 60);

  if (diffHours < 1) return `${Math.floor(diffMs / 60000)} minutes ago`;

  // Same calendar day?
  const nowLocal = new Date(
    now.toLocaleString("en-US", { timeZone: timezone }),
  );
  const dateLocal = new Date(
    date.toLocaleString("en-US", { timeZone: timezone }),
  );

  const sameDay =
    nowLocal.getFullYear() === dateLocal.getFullYear() &&
    nowLocal.getMonth() === dateLocal.getMonth() &&
    nowLocal.getDate() === dateLocal.getDate();

  if (sameDay) {
    return `Today at ${date.toLocaleTimeString("en-US", {
      timeZone: timezone,
      hour: "numeric",
      minute: "2-digit",
    })}`;
  }

  // Yesterday?
  const yesterdayLocal = new Date(nowLocal);
  yesterdayLocal.setDate(yesterdayLocal.getDate() - 1);
  const isYesterday =
    yesterdayLocal.getFullYear() === dateLocal.getFullYear() &&
    yesterdayLocal.getMonth() === dateLocal.getMonth() &&
    yesterdayLocal.getDate() === dateLocal.getDate();

  if (isYesterday) {
    return `Yesterday at ${date.toLocaleTimeString("en-US", {
      timeZone: timezone,
      hour: "numeric",
      minute: "2-digit",
    })}`;
  }

  // Otherwise: "3 days ago" or date
  const diffDays = Math.floor(diffHours / 24);
  if (diffDays < 7) {
    return `${diffDays} days ago`;
  }

  return date.toLocaleDateString("en-US", {
    timeZone: timezone,
    month: "short",
    day: "numeric",
  });
}

/**
 * Extract a rough topic from session messages using simple word frequency.
 * No LLM call — pure TF heuristic on user messages.
 */
function extractSessionTopic(messages: Array<{ role: string; content: string }>): string | null {
  const userMessages = messages
    .filter((m) => m.role === "user")
    .map((m) => m.content);

  if (userMessages.length === 0) return null;

  // Take last 5 user messages
  const text = userMessages.slice(-5).join(" ").toLowerCase();

  // Simple stopword-filtered word frequency
  const STOPWORDS = new Set([
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "i", "you", "he", "she",
    "it", "we", "they", "me", "him", "her", "us", "them", "my", "your",
    "his", "its", "our", "their", "this", "that", "these", "those",
    "what", "which", "who", "whom", "where", "when", "why", "how",
    "not", "no", "nor", "but", "or", "and", "if", "then", "else",
    "so", "too", "very", "just", "about", "up", "out", "on", "off",
    "over", "under", "again", "further", "once", "here", "there",
    "all", "each", "every", "both", "few", "more", "most", "some",
    "any", "other", "into", "through", "during", "before", "after",
    "above", "below", "to", "from", "with", "at", "by", "for", "of",
    "in", "as", "want", "need", "please", "help", "like", "get",
    "make", "know", "think", "go", "see", "come", "take", "give",
  ]);

  const words = text.match(/\b[a-z]{3,}\b/g) ?? [];
  const freq = new Map<string, number>();
  for (const w of words) {
    if (!STOPWORDS.has(w)) {
      freq.set(w, (freq.get(w) ?? 0) + 1);
    }
  }

  if (freq.size === 0) return null;

  // Top 3 words by frequency
  const top = [...freq.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3)
    .map(([w]) => w);

  return top.join(", ");
}

/**
 * Compute temporal context for the current message.
 * Zero LLM cost — pure arithmetic.
 */
export function computeTemporalContext(
  session: Session,
  previousSession: Session | null,
  timezone: string,
): TemporalSnapshot {
  const now = new Date();
  const localNow = new Date(
    now.toLocaleString("en-US", { timeZone: timezone }),
  );
  const hour = localNow.getHours();
  const day = localNow.getDay();
  const timeOfDay = getTimeOfDay(hour);
  const weekend = isWeekend(day);

  // Session age
  let sessionAge: string | null = null;
  if (session.metadata.startedAt) {
    const ageMs = now.getTime() - session.metadata.startedAt;
    if (ageMs > 30_000) {
      // Only show if > 30s
      sessionAge = formatDuration(ageMs);
    }
  }

  // Last message gap
  let lastMessageGap: string | null = null;
  let lastMessageGapMs = 0;
  if (session.messages.length > 0) {
    const lastMsg = session.messages[session.messages.length - 1];
    // Messages may have a timestamp property, or we use session lastUpdatedAt
    const lastMsgTime = (lastMsg as any).timestamp
      ? new Date((lastMsg as any).timestamp).getTime()
      : session.metadata.lastUpdatedAt;
    if (lastMsgTime) {
      lastMessageGapMs = now.getTime() - lastMsgTime;
      if (lastMessageGapMs > 30_000) {
        lastMessageGap = formatDuration(lastMessageGapMs);
      }
    }
  }

  // Previous session gap and topic
  let lastSessionGap: string | null = null;
  let lastSessionTopic: string | null = null;
  let lastSessionGapMs = 0;
  if (previousSession) {
    const prevEnd = previousSession.metadata.lastUpdatedAt;
    if (prevEnd) {
      lastSessionGapMs = now.getTime() - prevEnd;
      lastSessionGap = formatRelativeTime(new Date(prevEnd), now, timezone);
      lastSessionTopic = extractSessionTopic(previousSession.messages);
    }
  }

  // Returning user: gap > 4 hours from either last message or previous session
  const gapMs = Math.max(lastMessageGapMs, lastSessionGapMs);
  const isReturningUser = gapMs > 4 * 60 * 60 * 1000;

  return {
    now,
    timezone,
    dayOfWeek: DAYS[day],
    timeOfDay,
    dayContext: `${weekend ? "weekend" : "weekday"} ${timeOfDay}`,
    sessionAge,
    lastMessageGap,
    lastSessionGap,
    lastSessionTopic,
    isReturningUser,
  };
}

/**
 * Format temporal context for system prompt injection.
 * Only includes non-null fields — empty sections are omitted entirely.
 */
export function formatTemporalPrompt(snapshot: TemporalSnapshot): string {
  const lines: string[] = ["## Temporal Context"];

  // Current time
  const timeStr = snapshot.now.toLocaleString("en-US", {
    timeZone: snapshot.timezone,
    weekday: "long",
    year: "numeric",
    month: "long",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  });
  lines.push(`Current time: ${timeStr}`);

  if (snapshot.sessionAge) {
    lines.push(`Session started: ${snapshot.sessionAge} ago`);
  }
  if (snapshot.lastMessageGap) {
    lines.push(`Last user message: ${snapshot.lastMessageGap} ago`);
  }
  if (snapshot.lastSessionGap) {
    const topicStr = snapshot.lastSessionTopic
      ? ` (topic: ${snapshot.lastSessionTopic})`
      : "";
    lines.push(`Previous session: ${snapshot.lastSessionGap}${topicStr}`);
  }
  if (snapshot.isReturningUser) {
    lines.push(
      "Note: User is returning after a significant gap — acknowledge naturally and offer to resume prior work or start fresh.",
    );
  }

  return lines.join("\n");
}

/**
 * Load the previous session for a given session ID.
 * Finds the most recent session that isn't the current one.
 */
export async function loadPreviousSession(
  sessionStore: SessionStore,
  currentSessionId: string,
): Promise<Session | null> {
  try {
    const sessions = await sessionStore.listSessions();
    // Filter to same user prefix (e.g., "telegram:12345" sessions)
    const prefix = currentSessionId.split(":").slice(0, -1).join(":");
    const candidates = sessions
      .filter(
        (s) =>
          s.id !== currentSessionId &&
          (prefix ? s.id.startsWith(prefix) : true) &&
          s.messages.length > 0,
      )
      .sort(
        (a, b) =>
          (b.metadata.lastUpdatedAt ?? 0) - (a.metadata.lastUpdatedAt ?? 0),
      );

    return candidates[0] ?? null;
  } catch {
    return null;
  }
}
