import { describe, it, expect } from 'vitest';
import { CapabilityNeedAssessor } from '../src/evolution/assessor.js';
import type { Skill } from '../src/skills/types.js';

// Mock provider that returns a fixed response
function mockProvider(response: string) {
    return {
        chat: async () => ({ content: response, model: 'test', usage: undefined }),
    } as any;
}

const defaultTools = ['run_shell_command', 'read_file', 'write_file', 'web_crawl'];

function makeSkill(name: string, description: string): Skill {
    return { name, description, instructions: 'test', filePath: '/test', metadata: {} };
}

describe('CapabilityNeedAssessor', () => {
    describe('Heuristic Pre-Filters', () => {
        it('should SKIP conversational messages', async () => {
            const assessor = new CapabilityNeedAssessor(mockProvider(''));
            const result = await assessor.assess('hello!', defaultTools, []);
            expect(result.verdict).toBe('SKIP');
            expect(result.requestType).toBe('CONVERSATIONAL');
        });

        it('should SKIP thanks messages', async () => {
            const assessor = new CapabilityNeedAssessor(mockProvider(''));
            const result = await assessor.assess('thanks!', defaultTools, []);
            expect(result.verdict).toBe('SKIP');
        });

        it('should SKIP informational questions', async () => {
            const assessor = new CapabilityNeedAssessor(mockProvider(''));
            const result = await assessor.assess('what is machine learning?', defaultTools, []);
            expect(result.verdict).toBe('SKIP');
            expect(result.requestType).toBe('INFORMATIONAL');
        });

        it('should SKIP "explain" requests', async () => {
            const assessor = new CapabilityNeedAssessor(mockProvider(''));
            const result = await assessor.assess('explain how neural networks work', defaultTools, []);
            expect(result.verdict).toBe('SKIP');
            expect(result.requestType).toBe('INFORMATIONAL');
        });

        it('should detect NEAR_DUPLICATE when existing skill has high overlap', async () => {
            const skills = [makeSkill('take_screenshot', 'Takes a screenshot of the current screen')];
            const assessor = new CapabilityNeedAssessor(mockProvider(''));
            const result = await assessor.assess(
                'take a screenshot of my screen',
                defaultTools,
                skills,
            );
            expect(result.verdict).toBe('NEAR_DUPLICATE');
            expect(result.suggestedExistingSkill).toBe('take_screenshot');
        });
    });

    describe('Gap Description Fast-Path', () => {
        it('should SYNTHESIZE when engine declares a gap', async () => {
            const assessor = new CapabilityNeedAssessor(mockProvider(''));
            const result = await assessor.assess(
                'control the Chrome browser programmatically',
                defaultTools,
                [],
                'Need ability to programmatically control Chrome browser',
            );
            expect(result.verdict).toBe('SYNTHESIZE');
            expect(result.requestType).toBe('OPERATIONAL');
        });
    });

    describe('LLM-Based Assessment', () => {
        it('should SYNTHESIZE when LLM says not covered', async () => {
            const assessor = new CapabilityNeedAssessor(
                mockProvider(JSON.stringify({
                    request_type: 'OPERATIONAL',
                    covered_by_existing: false,
                    covering_skill_or_tool: null,
                    reasoning: 'No existing tool can send Telegram messages.',
                })),
            );
            const result = await assessor.assess(
                'send a message on Telegram',
                defaultTools,
                [],
            );
            expect(result.verdict).toBe('SYNTHESIZE');
        });

        it('should COVERED when LLM says existing tools handle it', async () => {
            const assessor = new CapabilityNeedAssessor(
                mockProvider(JSON.stringify({
                    request_type: 'OPERATIONAL',
                    covered_by_existing: true,
                    covering_skill_or_tool: 'run_shell_command',
                    reasoning: 'Can use shell command to run the task.',
                })),
            );
            const result = await assessor.assess(
                'list files in the current directory',
                defaultTools,
                [],
            );
            expect(result.verdict).toBe('COVERED');
            expect(result.suggestedExistingSkill).toBe('run_shell_command');
        });

        it('should fallback to SYNTHESIZE on LLM error', async () => {
            const assessor = new CapabilityNeedAssessor({
                chat: async () => { throw new Error('LLM down'); },
            } as any);
            const result = await assessor.assess(
                'send an email to the team',
                defaultTools,
                [],
            );
            expect(result.verdict).toBe('SYNTHESIZE');
        });
    });
});
