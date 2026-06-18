/**
 * StackOwl — Tool Mastery
 *
 * Tracks per-tool mastery levels and adjusts confidence accordingly.
 * Provides self-awareness of tool proficiency for better delegation decisions.
 */

export type MasteryLevel = "novice" | "intermediate" | "expert" | "master";

export interface ToolMasteryProfile {
  toolName: string;
  masteryLevel: MasteryLevel;
  confidenceMultiplier: number;
  totalAttempts: number;
  successRate: number;
}

export class ToolMastery {
  private masteryProfiles: Map<string, ToolMasteryProfile> = new Map();

  private calculateMasteryLevel(
    totalAttempts: number,
    successRate: number,
  ): MasteryLevel {
    if (totalAttempts < 3) return "novice";
    if (successRate >= 0.9 && totalAttempts >= 20) return "master";
    if (successRate >= 0.75 && totalAttempts >= 10) return "expert";
    if (totalAttempts >= 5 || successRate >= 0.5) return "intermediate";
    return "novice";
  }

  private confidenceMultiplier(level: MasteryLevel): number {
    const multipliers: Record<MasteryLevel, number> = {
      novice: 0.6,
      intermediate: 0.8,
      expert: 1.0,
      master: 1.2,
    };
    return multipliers[level];
  }

  recordAttempt(toolName: string, success: boolean): void {
    const profile = this.masteryProfiles.get(toolName) ?? {
      toolName,
      masteryLevel: "novice" as MasteryLevel,
      confidenceMultiplier: 0.6,
      totalAttempts: 0,
      successRate: 0,
    };

    profile.totalAttempts++;
    profile.successRate =
      (profile.successRate * (profile.totalAttempts - 1) +
        (success ? 1 : 0)) /
      profile.totalAttempts;
    profile.masteryLevel = this.calculateMasteryLevel(
      profile.totalAttempts,
      profile.successRate,
    );
    profile.confidenceMultiplier = this.confidenceMultiplier(
      profile.masteryLevel,
    );

    this.masteryProfiles.set(toolName, profile);
  }

  getMasteryProfile(toolName: string): ToolMasteryProfile {
    return (
      this.masteryProfiles.get(toolName) ?? {
        toolName,
        masteryLevel: "novice",
        confidenceMultiplier: 0.6,
        totalAttempts: 0,
        successRate: 0,
      }
    );
  }

  getConfidenceMultiplier(toolName: string): number {
    return this.getMasteryProfile(toolName).confidenceMultiplier;
  }

  getMasteryLevel(toolName: string): MasteryLevel {
    return this.getMasteryProfile(toolName).masteryLevel;
  }

  getAllMasteryProfiles(): ToolMasteryProfile[] {
    return Array.from(this.masteryProfiles.values());
  }
}
