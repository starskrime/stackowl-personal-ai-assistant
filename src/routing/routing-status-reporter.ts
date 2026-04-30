// src/routing/routing-status-reporter.ts
import type { MemoryDatabase, OwlTask, OwlJob } from "../memory/db.js";

export interface StatusReport {
  activePin?: string;
  openTasks: { id: string; title: string; status: string; priority: string; dueAt?: string }[];
  queuedJobs: { id: string; type: string; scheduledAt: string }[];
  lastRoutingDecision?: { owl: string; reason: string; ts: string };
}

const STATUS_PATTERNS = [
  /what\s+are\s+you\s+(working\s+on|doing|up\s+to)/i,
  /what\s+tasks?\b/i,
  /what\s+did\s+you\s+(promise|commit|say\s+you.d)/i,
  /what.s\s+pending/i,
  /\bmy\s+(tasks?|status|commitments?)\b/i,
  /open\s+tasks?/i,
];

export class RoutingStatusReporter {
  constructor(private db: Pick<MemoryDatabase, "userProfiles" | "owlTasks" | "owlJobs">) {}

  static isStatusQuery(text: string): boolean {
    const lower = text.toLowerCase().trim();
    return STATUS_PATTERNS.some((p) => p.test(lower));
  }

  getStatusReport(userId: string): StatusReport {
    const pin = this.db.userProfiles.getPin(userId);
    const tasks = this.db.owlTasks.getActive(userId).map((t: OwlTask) => ({
      id: t.id, title: t.title, status: t.status, priority: t.priority, dueAt: t.dueAt,
    }));
    const jobs = this.db.owlJobs.getQueued(userId).slice(0, 5).map((j: OwlJob) => ({
      id: j.id, type: j.type, scheduledAt: j.scheduledAt,
    }));
    const history = this.db.userProfiles.getRoutingHistory(userId);
    const last = history.length > 0 ? history[history.length - 1] : undefined;

    return {
      activePin: pin ?? undefined,
      openTasks: tasks,
      queuedJobs: jobs,
      lastRoutingDecision: last ? { owl: last.owl, reason: last.reason, ts: last.ts } : undefined,
    };
  }

  formatForChannel(report: StatusReport, _channelId: string): string {
    const lines: string[] = [];

    if (report.activePin) {
      lines.push(`**Active specialist:** @${report.activePin}`);
    } else {
      lines.push("**Active specialist:** coordinator (default)");
    }

    if (report.openTasks.length > 0) {
      lines.push("\n**Open tasks:**");
      for (const t of report.openTasks) {
        const due = t.dueAt ? ` (due ${t.dueAt.slice(0, 10)})` : "";
        lines.push(`- [${t.priority}] ${t.title} — *${t.status}*${due}`);
      }
    } else {
      lines.push("\n**Open tasks:** none");
    }

    if (report.queuedJobs.length > 0) {
      lines.push("\n**Queued jobs:**");
      for (const j of report.queuedJobs) {
        lines.push(`- ${j.type} (scheduled ${j.scheduledAt.slice(0, 16)})`);
      }
    }

    if (report.lastRoutingDecision) {
      lines.push(`\n**Last routing:** @${report.lastRoutingDecision.owl} — ${report.lastRoutingDecision.reason}`);
    }

    return lines.join("\n");
  }
}
