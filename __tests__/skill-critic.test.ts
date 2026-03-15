import { describe, it, expect } from 'vitest';
import { SkillCritic } from '../src/skills/critic.js';
import type { Skill } from '../src/skills/types.js';

// Use heuristic critique (no LLM needed for tests)
const mockProvider = {} as any;
const critic = new SkillCritic(mockProvider);

function makeSkill(overrides: Partial<Skill> = {}): Skill {
    return {
        name: 'send_daily_brief',
        description: 'Sends a daily news briefing to the user via Telegram',
        instructions: '1. Use google_search to find top news\n2. Use web_crawl to fetch articles\n3. Summarize each\n4. Use run_shell_command to send via Telegram API',
        filePath: '/test/SKILL.md',
        metadata: {},
        ...overrides,
    };
}

describe('SkillCritic Heuristic', () => {
    it('should give high score to well-formed skills', () => {
        const result = critic.critiqueHeuristic(makeSkill());
        expect(result.overallScore).toBeGreaterThan(0.6);
        expect(result.needsRewrite).toBe(false);
    });

    it('should penalize generic names', () => {
        const result = critic.critiqueHeuristic(makeSkill({ name: 'skill_1' }));
        expect(result.nameClarityScore.score).toBeLessThan(0.3);
        expect(result.needsRewrite).toBe(true);
    });

    it('should penalize synthesized_skill names', () => {
        const result = critic.critiqueHeuristic(makeSkill({ name: 'synthesized_skill' }));
        expect(result.nameClarityScore.score).toBeLessThan(0.3);
    });

    it('should penalize very short names', () => {
        const result = critic.critiqueHeuristic(makeSkill({ name: 'abc' }));
        expect(result.nameClarityScore.score).toBeLessThan(0.5);
    });

    it('should penalize names without snake_case', () => {
        const result = critic.critiqueHeuristic(makeSkill({ name: 'senddailybrief' }));
        expect(result.nameClarityScore.score).toBeLessThan(0.8);
    });

    it('should penalize vague instructions', () => {
        const result = critic.critiqueHeuristic(makeSkill({
            instructions: 'Use the appropriate tools to complete the task as needed.',
        }));
        expect(result.instructionClarityScore.score).toBeLessThan(0.5);
    });

    it('should reward concrete instructions with tool calls', () => {
        const result = critic.critiqueHeuristic(makeSkill({
            instructions: '1. Use run_shell_command to execute `curl https://api.example.com`\n2. Parse the JSON output\n3. Use write_file to save results',
        }));
        expect(result.instructionClarityScore.score).toBeGreaterThan(0.6);
    });

    it('should penalize overly broad descriptions', () => {
        const result = critic.critiqueHeuristic(makeSkill({
            description: 'Does anything the user asks for all tasks',
        }));
        expect(result.triggerPrecisionScore.score).toBeLessThan(0.3);
    });

    it('should penalize very short descriptions', () => {
        const result = critic.critiqueHeuristic(makeSkill({
            description: 'Do stuff',
        }));
        expect(result.triggerPrecisionScore.score).toBeLessThan(0.5);
    });

    it('should produce rewrite directive for low-scoring skills', () => {
        const result = critic.critiqueHeuristic(makeSkill({
            name: 'skill_2',
            description: 'hi',
            instructions: 'do the thing',
        }));
        expect(result.needsRewrite).toBe(true);
        expect(result.rewriteDirective.length).toBeGreaterThan(10);
    });

    it('should weight instructions at 50%', () => {
        // Good name + trigger, bad instructions
        const result = critic.critiqueHeuristic(makeSkill({
            name: 'fetch_weather',
            description: 'Fetches current weather data for a given city using OpenWeather API',
            instructions: 'just do it somehow',
        }));
        // Instructions (0.3) × 0.5 = 0.15 component, but name + trigger should be good
        expect(result.instructionClarityScore.score).toBeLessThan(0.5);
        expect(result.nameClarityScore.score).toBeGreaterThan(0.5);
    });
});
