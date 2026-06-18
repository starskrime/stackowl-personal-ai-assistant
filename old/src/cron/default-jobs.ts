import type { CronJob } from "./types.js";

export const DEFAULT_CRON_JOBS: CronJob[] = [
  {
    id: "memory-consolidation",
    schedule: "0 * * * *",
    prompt:
      "Consolidate recent episodic memories: compress old episodes, archive those older than 30 days, " +
      "and log how many were compressed and archived.",
    safetyProfile: "low",
    deliver: false,
    description: "Hourly memory compression and archiving",
  },
  {
    id: "desire-execution",
    schedule: "*/30 * * * *",
    prompt:
      "Review the top pending owl desire and execute it if actionable and low-risk. " +
      "Report what was done or why it was deferred.",
    safetyProfile: "medium",
    deliver: false,
    description: "Every 30 min: process top owl desire",
  },
  {
    id: "dna-evolution",
    schedule: "0 2 * * *",
    prompt:
      "Review the owl's recent interaction patterns (last 24 hours) and suggest specific DNA trait " +
      "adjustments: challengeLevel, verbosity, creativity, riskTolerance. Output a JSON diff.",
    safetyProfile: "low",
    deliver: false,
    description: "Nightly DNA evolution at 2am",
  },
  {
    id: "pellet-dedup",
    schedule: "0 3 * * *",
    prompt:
      "Scan the knowledge pellet store for near-duplicate entries (cosine similarity > 0.92). " +
      "Merge duplicates into the more recent entry and report how many were removed.",
    safetyProfile: "low",
    deliver: false,
    description: "Nightly pellet deduplication at 3am",
  },
  {
    id: "daily-briefing",
    schedule: "0 9 * * *",
    prompt:
      "Generate a concise morning briefing for the user: " +
      "what happened yesterday (from recent memory), any open goals or desires, " +
      "and 1-2 proactive suggestions for today. Keep it under 200 words.",
    safetyProfile: "low",
    deliver: true,
    description: "Daily morning briefing at 9am",
  },
];
