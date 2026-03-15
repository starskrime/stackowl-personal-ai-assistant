import { randomUUID } from 'node:crypto';
import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { Logger } from '../logger.js';
import type { ModelProvider, ChatMessage } from '../providers/base.js';
import type { UserPattern, PredictiveConfig, DayOfWeek, TimeSlot } from './types.js';

const log = new Logger('PREDICTIVE');

interface Session {
  id: string;
  messages: { role: string; content: string }[];
  metadata: { owlName: string; startedAt: number; lastUpdatedAt: number; title?: string };
}

const DEFAULT_CONFIG: PredictiveConfig = {
  minPatternFrequency: 3,
  predictionHorizonHours: 24,
  maxQueuedTasks: 10,
  minConfidence: 0.6,
};

const DAYS: DayOfWeek[] = ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'];

function getTimeSlot(hour: number): TimeSlot {
  if (hour < 6) return 'night';
  if (hour < 9) return 'early_morning';
  if (hour < 12) return 'morning';
  if (hour < 17) return 'afternoon';
  if (hour < 21) return 'evening';
  return 'night';
}

export class PatternAnalyzer {
  private patterns = new Map<string, UserPattern>();
  private filePath: string;
  private config: PredictiveConfig;

  constructor(
    private provider: ModelProvider,
    private workspacePath: string,
    config?: Partial<PredictiveConfig>
  ) {
    this.config = { ...DEFAULT_CONFIG, ...config };
    this.filePath = join(workspacePath, 'user-patterns.json');
  }

  async load(): Promise<void> {
    try {
      if (!existsSync(this.filePath)) {
        log.debug('No existing patterns found, starting fresh');
        return;
      }
      const raw = readFileSync(this.filePath, 'utf-8');
      const data: UserPattern[] = JSON.parse(raw);
      this.patterns.clear();
      for (const pattern of data) {
        this.patterns.set(pattern.id, pattern);
      }
      log.info(`Loaded ${this.patterns.size} user patterns`);
    } catch (err) {
      log.error(`Failed to load patterns: ${err}`);
    }
  }

  async analyzeHistory(sessions: Session[]): Promise<UserPattern[]> {
    if (sessions.length === 0) return [];

    const grouped: Record<string, { day: DayOfWeek; slot: TimeSlot; content: string }[]> = {};

    for (const session of sessions) {
      const date = new Date(session.metadata.startedAt);
      const day = DAYS[date.getDay()];
      const slot = getTimeSlot(date.getHours());

      for (const msg of session.messages) {
        if (msg.role !== 'user') continue;
        const key = `${day}-${slot}`;
        if (!grouped[key]) grouped[key] = [];
        grouped[key].push({ day, slot, content: msg.content });
      }
    }

    const groupedText = Object.entries(grouped)
      .map(([key, msgs]) => `${key} (${msgs.length} messages):\n${msgs.map(m => `  - ${m.content.slice(0, 100)}`).join('\n')}`)
      .join('\n\n');

    if (!groupedText.trim()) return [];

    const prompt = `Analyze these user interaction patterns and identify recurring behaviors.

User messages by time slot:
${groupedText}

For each pattern found, output JSON array only, no other text:
[{
  "action": "checks email and news",
  "dayPreference": ["monday", "tuesday", "wednesday", "thursday", "friday"],
  "timePreference": ["morning"],
  "avgIntervalHours": 24,
  "confidence": 0.8
}]

Only include patterns with ${this.config.minPatternFrequency}+ occurrences.`;

    try {
      const messages: ChatMessage[] = [
        { role: 'system', content: 'You are a behavior analysis engine. Output valid JSON only.' },
        { role: 'user', content: prompt },
      ];

      const response = await this.provider.chat(messages, undefined, { temperature: 0.2 });
      const cleaned = response.content.replace(/```json\s*/g, '').replace(/```\s*/g, '').trim();
      const discovered: Array<{
        action: string;
        dayPreference: DayOfWeek[];
        timePreference: TimeSlot[];
        avgIntervalHours: number;
        confidence: number;
      }> = JSON.parse(cleaned);

      if (!Array.isArray(discovered)) return this.getPatterns();

      for (const d of discovered) {
        const existing = this.findMatchingPattern(d.action);
        if (existing) {
          existing.frequency++;
          existing.confidence = Math.max(existing.confidence, d.confidence);
          existing.avgIntervalHours = (existing.avgIntervalHours + d.avgIntervalHours) / 2;
          existing.dayPreference = [...new Set([...existing.dayPreference, ...d.dayPreference])];
          existing.timePreference = [...new Set([...existing.timePreference, ...d.timePreference])];
        } else {
          const pattern: UserPattern = {
            id: randomUUID(),
            action: d.action,
            frequency: this.config.minPatternFrequency,
            dayPreference: d.dayPreference,
            timePreference: d.timePreference,
            lastOccurred: new Date().toISOString(),
            avgIntervalHours: d.avgIntervalHours,
            confidence: d.confidence,
            relatedSkills: [],
          };
          this.patterns.set(pattern.id, pattern);
        }
      }

      log.info(`Analyzed history: ${this.patterns.size} total patterns`);
      return this.getPatterns();
    } catch (err) {
      log.error(`Pattern analysis failed: ${err}`);
      return this.getPatterns();
    }
  }

  recordAction(action: string, skills: string[]): void {
    const existing = this.findMatchingPattern(action);
    const now = new Date();

    if (existing) {
      const lastTime = new Date(existing.lastOccurred).getTime();
      const hoursSince = (now.getTime() - lastTime) / (1000 * 60 * 60);
      existing.avgIntervalHours = (existing.avgIntervalHours * existing.frequency + hoursSince) / (existing.frequency + 1);
      existing.frequency++;
      existing.lastOccurred = now.toISOString();
      existing.relatedSkills = [...new Set([...existing.relatedSkills, ...skills])];

      const day = DAYS[now.getDay()];
      if (!existing.dayPreference.includes(day)) {
        existing.dayPreference.push(day);
      }
      const slot = getTimeSlot(now.getHours());
      if (!existing.timePreference.includes(slot)) {
        existing.timePreference.push(slot);
      }
    } else {
      const pattern: UserPattern = {
        id: randomUUID(),
        action,
        frequency: 1,
        dayPreference: [DAYS[now.getDay()]],
        timePreference: [getTimeSlot(now.getHours())],
        lastOccurred: now.toISOString(),
        avgIntervalHours: 24,
        confidence: 0.3,
        relatedSkills: skills,
      };
      this.patterns.set(pattern.id, pattern);
    }
  }

  getPatterns(): UserPattern[] {
    return Array.from(this.patterns.values());
  }

  getUpcoming(hours?: number): UserPattern[] {
    const horizon = hours ?? this.config.predictionHorizonHours;
    const now = Date.now();
    const horizonMs = horizon * 60 * 60 * 1000;

    const upcoming: { pattern: UserPattern; predictedTime: number }[] = [];

    for (const pattern of this.patterns.values()) {
      if (pattern.frequency < this.config.minPatternFrequency) continue;
      if (pattern.confidence < this.config.minConfidence) continue;

      const lastTime = new Date(pattern.lastOccurred).getTime();
      const nextExpected = lastTime + pattern.avgIntervalHours * 60 * 60 * 1000;

      if (nextExpected <= now + horizonMs && nextExpected >= now - horizonMs / 2) {
        upcoming.push({ pattern, predictedTime: nextExpected });
      }
    }

    upcoming.sort((a, b) => a.predictedTime - b.predictedTime);
    return upcoming.map(u => u.pattern);
  }

  async save(): Promise<void> {
    try {
      if (!existsSync(this.workspacePath)) {
        mkdirSync(this.workspacePath, { recursive: true });
      }
      const data = Array.from(this.patterns.values());
      writeFileSync(this.filePath, JSON.stringify(data, null, 2), 'utf-8');
      log.debug(`Saved ${data.length} user patterns`);
    } catch (err) {
      log.error(`Failed to save patterns: ${err}`);
    }
  }

  /**
   * Enrich patterns with cross-system signals from a user profile.
   * Called periodically to boost confidence of patterns that align
   * with the user's overall behavior profile.
   */
  enrichFromProfile(profile: {
    topics: Record<string, number>;
    toolUsage: Record<string, number>;
    hourlyActivity: number[];
  }): void {
    for (const pattern of this.patterns.values()) {
      const actionLower = pattern.action.toLowerCase();

      // Boost confidence if the pattern's action aligns with frequent topics
      for (const [topic, count] of Object.entries(profile.topics)) {
        if (actionLower.includes(topic) && count >= 3) {
          const boost = Math.min(0.1, count * 0.01);
          pattern.confidence = Math.min(0.95, pattern.confidence + boost);
        }
      }

      // Boost confidence if the pattern's skills are frequently used
      for (const skill of pattern.relatedSkills) {
        const usage = profile.toolUsage[skill] ?? 0;
        if (usage >= 3) {
          const boost = Math.min(0.05, usage * 0.005);
          pattern.confidence = Math.min(0.95, pattern.confidence + boost);
        }
      }

      // Refine time preferences using hourly activity data
      const totalActivity = profile.hourlyActivity.reduce((s, v) => s + v, 0);
      if (totalActivity > 20) {
        const avg = totalActivity / 24;
        const peakSlots: Set<TimeSlot> = new Set();
        for (let h = 0; h < 24; h++) {
          if (profile.hourlyActivity[h] > avg * 1.5) {
            peakSlots.add(getTimeSlot(h));
          }
        }
        // If the pattern doesn't have time preferences but we know peak times,
        // use the peak times as a hint
        if (pattern.timePreference.length === 0 && peakSlots.size > 0) {
          pattern.timePreference = Array.from(peakSlots);
        }
      }
    }
  }

  private findMatchingPattern(action: string): UserPattern | undefined {
    const lowerAction = action.toLowerCase();
    for (const pattern of this.patterns.values()) {
      if (pattern.action.toLowerCase() === lowerAction) return pattern;
    }
    return undefined;
  }
}
