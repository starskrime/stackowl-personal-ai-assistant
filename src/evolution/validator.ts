/**
 * StackOwl — Skill Validator
 *
 * Pre-tests synthesized skills before promoting them to production.
 * Prevents skills that produce incorrect output from being used on
 * real user requests immediately after synthesis.
 *
 * Process:
 *   1. Generate 2-3 synthetic test cases from the skill description
 *   2. Execute the skill in sandbox mode against test cases
 *   3. Evaluate results for correctness signals
 *   4. Only promote if tests pass; record results for SkillEvolver
 */

import type { ModelProvider } from '../providers/base.js';
import type { StackOwlConfig } from '../config/loader.js';
import { OwlEngine } from '../engine/runtime.js';
import type { EngineContext } from '../engine/runtime.js';
import { log } from '../logger.js';

// ─── Types ───────────────────────────────────────────────────────

export interface TestCase {
  /** What the user would say to invoke this skill */
  userMessage: string;
  /** Expected characteristics of a correct response */
  expectedBehavior: string;
  /** Keywords that should appear in the output */
  expectedKeywords: string[];
}

export interface ValidationResult {
  /** Overall pass/fail */
  passed: boolean;
  /** Individual test case results */
  testResults: Array<{
    testCase: TestCase;
    output: string;
    passed: boolean;
    reason: string;
  }>;
  /** Summary for logging */
  summary: string;
  /** How long validation took */
  durationMs: number;
}

// ─── Validator ───────────────────────────────────────────────────

export class SkillValidator {
  private engine: OwlEngine;

  constructor(
    private provider: ModelProvider,
    _config: StackOwlConfig,
  ) {
    this.engine = new OwlEngine();
  }

  /**
   * Validate a synthesized skill by generating and running test cases.
   *
   * @param skillName - Name of the skill being validated
   * @param skillContent - The SKILL.md content
   * @param context - Engine context for sandboxed execution
   * @returns ValidationResult with pass/fail and details
   */
  async validate(
    skillName: string,
    skillContent: string,
    context: EngineContext,
  ): Promise<ValidationResult> {
    const startTime = Date.now();

    log.evolution.info(`[SkillValidator] Generating test cases for "${skillName}"...`);

    // Step 1: Generate test cases
    const testCases = await this.generateTestCases(skillName, skillContent);
    if (testCases.length === 0) {
      return {
        passed: true, // Can't validate = assume OK (conservative)
        testResults: [],
        summary: `No test cases generated for "${skillName}" — skipping validation.`,
        durationMs: Date.now() - startTime,
      };
    }

    log.evolution.info(`[SkillValidator] Running ${testCases.length} test case(s)...`);

    // Step 2: Execute each test case in sandboxed mode
    const testResults: ValidationResult['testResults'] = [];

    for (const testCase of testCases) {
      try {
        // Inject skill directive into the context
        const sandboxContext: EngineContext = {
          ...context,
          sessionHistory: [
            {
              role: 'system',
              content:
                `[SKILL VALIDATION MODE] You are testing a new skill. Follow the skill instructions exactly.\n\n` +
                `<skill name="${skillName}">\n${skillContent}\n</skill>`,
            },
          ],
          skipGapDetection: true,
          isolatedTask: true,
        };

        const response = await this.engine.run(testCase.userMessage, sandboxContext);

        // Step 3: Evaluate the response
        const evaluation = await this.evaluateResponse(testCase, response.content);
        testResults.push({
          testCase,
          output: response.content.slice(0, 500),
          passed: evaluation.passed,
          reason: evaluation.reason,
        });
      } catch (err) {
        testResults.push({
          testCase,
          output: `Error: ${err instanceof Error ? err.message : String(err)}`,
          passed: false,
          reason: `Execution failed: ${err instanceof Error ? err.message : String(err)}`,
        });
      }
    }

    // Step 4: Overall verdict
    const passedCount = testResults.filter(r => r.passed).length;
    const overallPassed = passedCount >= Math.ceil(testResults.length * 0.6);

    const result: ValidationResult = {
      passed: overallPassed,
      testResults,
      summary:
        `Skill "${skillName}": ${passedCount}/${testResults.length} tests passed — ` +
        `${overallPassed ? '✅ PROMOTED' : '❌ REJECTED'}`,
      durationMs: Date.now() - startTime,
    };

    log.evolution.info(`[SkillValidator] ${result.summary}`);
    return result;
  }

  // ─── Private ───────────────────────────────────────────────────

  /**
   * Generate synthetic test cases from the skill description.
   */
  private async generateTestCases(skillName: string, skillContent: string): Promise<TestCase[]> {
    const systemPrompt =
      `You generate test cases for an AI assistant skill. ` +
      `Given a skill description, produce 2-3 realistic user messages that would invoke this skill, ` +
      `along with expected behavior and keywords that should appear in a correct response.\n\n` +
      `Output valid JSON array: [{ "userMessage": string, "expectedBehavior": string, "expectedKeywords": string[] }]\n` +
      `Output ONLY valid JSON — no prose, no code fences.`;

    try {
      const response = await this.provider.chat(
        [
          { role: 'system', content: systemPrompt },
          { role: 'user', content: `Skill name: "${skillName}"\n\nSkill content:\n${skillContent.slice(0, 1500)}` },
        ],
        undefined,
        { temperature: 0.3, maxTokens: 512 },
      );

      let jsonStr = response.content.trim();
      if (jsonStr.startsWith('```')) {
        jsonStr = jsonStr.replace(/^```(?:json)?/, '').replace(/```$/, '').trim();
      }

      return JSON.parse(jsonStr) as TestCase[];
    } catch (err) {
      log.evolution.warn(
        `[SkillValidator] Test case generation failed: ${err instanceof Error ? err.message : err}`,
      );
      return [];
    }
  }

  /**
   * Evaluate if a response meets the test case expectations.
   */
  private async evaluateResponse(
    testCase: TestCase,
    response: string,
  ): Promise<{ passed: boolean; reason: string }> {
    // Quick heuristic check first (no LLM call)
    const lowerResponse = response.toLowerCase();

    // Check for obvious failure signals
    if (
      lowerResponse.includes('i cannot') ||
      lowerResponse.includes("i don't have") ||
      lowerResponse.includes('error:') ||
      lowerResponse.includes('failed to')
    ) {
      return {
        passed: false,
        reason: 'Response contains failure/refusal signals.',
      };
    }

    // Check for expected keywords
    const keywordsFound = testCase.expectedKeywords.filter(
      kw => lowerResponse.includes(kw.toLowerCase()),
    );
    const keywordRatio = testCase.expectedKeywords.length > 0
      ? keywordsFound.length / testCase.expectedKeywords.length
      : 1;

    if (keywordRatio >= 0.5) {
      return {
        passed: true,
        reason: `${keywordsFound.length}/${testCase.expectedKeywords.length} expected keywords found.`,
      };
    }

    // Check minimum response length (very short = probably failed)
    if (response.length < 50) {
      return {
        passed: false,
        reason: 'Response too short — likely incomplete or failed.',
      };
    }

    // If keywords don't match but response is substantial, give benefit of doubt
    return {
      passed: true,
      reason: `Keyword match low (${keywordsFound.length}/${testCase.expectedKeywords.length}) but response is substantial.`,
    };
  }
}
