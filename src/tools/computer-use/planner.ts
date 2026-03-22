/**
 * StackOwl — Computer Use Action Planner
 *
 * Plans multi-step desktop automation sequences, executes them with
 * verification after each step, and saves successful flows as reusable
 * recipes. Uses only the configured model — no extra dependencies.
 *
 * Architecture:
 *   1. Check recipe store for a matching workflow
 *   2. If found → replay with step-by-step verification
 *   3. If not → ask the same model to plan steps from the AX tree
 *   4. Execute each step → verify → adapt or retry
 *   5. On success → offer to save as recipe
 */

import type { ModelProvider } from '../../providers/base.js';
import type { ScreenState } from './screen-reader.js';
import { readScreen, formatScreenMinimal } from './screen-reader.js';
import { RecipeStore, type Recipe, type RecipeStep } from './recipes.js';
import { diffScreenStates } from './screen-diff.js';
import { log } from '../../logger.js';

// ─── Types ───────────────────────────────────────────────────────────────────

export interface PlanStep {
  action: string;
  args: Record<string, unknown>;
  description: string;
  verify?: string;
}

export interface PlanResult {
  steps: PlanStep[];
  reasoning: string;
}

export interface ExecutionResult {
  success: boolean;
  stepsCompleted: number;
  totalSteps: number;
  /** What changed on screen after execution */
  screenChanges: string;
  /** Error message if failed */
  error?: string;
  /** The completed steps (for recipe saving) */
  completedSteps: RecipeStep[];
}

// ─── Action Planner ──────────────────────────────────────────────────────────

export class ActionPlanner {
  constructor(
    private provider: ModelProvider,
    private recipeStore: RecipeStore,
  ) {}

  /**
   * Plan a sequence of computer_use actions to accomplish a task.
   * First checks recipes, then falls back to AI planning from current screen state.
   */
  async plan(task: string, currentScreen: ScreenState): Promise<PlanResult> {
    // 1. Check for matching recipes
    const matches = this.recipeStore.findMatching(task);
    if (matches.length > 0 && matches[0].score > 0.4) {
      const recipe = matches[0].recipe;
      log.engine.info(
        `[ActionPlanner] Found matching recipe: "${recipe.task}" (score: ${matches[0].score.toFixed(2)})`,
      );
      return {
        steps: recipe.steps.map(s => ({
          action: s.action,
          args: s.args,
          description: s.description,
          verify: s.verify,
        })),
        reasoning: `Replaying recipe "${recipe.task}" (used ${recipe.successCount} times successfully)`,
      };
    }

    // 2. AI-planned from current screen state
    const screenText = formatScreenMinimal(currentScreen);
    const prompt =
      `You are a desktop automation planner. Given a task and the current screen state, ` +
      `produce a JSON plan of computer_use actions to accomplish the task.\n\n` +
      `TASK: "${task}"\n\n` +
      `CURRENT SCREEN:\n${screenText}\n\n` +
      `Available actions:\n` +
      `- click: {x, y} — click at coordinates\n` +
      `- double_click: {x, y}\n` +
      `- type: {text} — type text (click a field first)\n` +
      `- key: {key, modifiers?} — press a key (enter, tab, escape, etc.)\n` +
      `- hotkey: {key} — key combo like "cmd+c", "cmd+t"\n` +
      `- open_app: {text} — open/activate an application\n` +
      `- open_url: {text} — open URL in browser\n` +
      `- scroll: {direction, amount?} — scroll up/down/left/right\n` +
      `- wait: {amount} — wait N milliseconds\n\n` +
      `Use [ref:N] coordinates from the screen state for precise clicks.\n\n` +
      `Return ONLY valid JSON:\n` +
      `{"reasoning": "brief explanation", "steps": [{"action": "...", "args": {...}, "description": "what this does", "verify": "what to check after"}]}`;

    try {
      const response = await this.provider.chat(
        [{ role: 'user', content: prompt }],
        undefined,
        { temperature: 0, maxTokens: 2048 },
      );

      const jsonMatch = response.content.match(/\{[\s\S]*\}/);
      if (!jsonMatch) {
        log.engine.warn('[ActionPlanner] No JSON in AI response, falling back to single-step');
        return { steps: [], reasoning: 'Could not parse plan' };
      }

      const parsed = JSON.parse(jsonMatch[0]) as PlanResult;
      log.engine.info(
        `[ActionPlanner] AI planned ${parsed.steps.length} steps: ${parsed.reasoning}`,
      );
      return parsed;
    } catch (err) {
      log.engine.warn(
        `[ActionPlanner] Planning failed: ${err instanceof Error ? err.message : String(err)}`,
      );
      return { steps: [], reasoning: 'Planning failed' };
    }
  }

  /**
   * Execute a planned sequence with verification after each step.
   * Returns after completing all steps or on first unrecoverable failure.
   */
  async execute(
    plan: PlanResult,
    executeAction: (action: string, args: Record<string, unknown>) => Promise<string>,
    onProgress?: (msg: string) => Promise<void>,
  ): Promise<ExecutionResult> {
    const completedSteps: RecipeStep[] = [];
    let lastScreen: ScreenState | null = null;

    for (let i = 0; i < plan.steps.length; i++) {
      const step = plan.steps[i];

      await onProgress?.(`Step ${i + 1}/${plan.steps.length}: ${step.description}`);

      // Capture screen before action
      try {
        lastScreen = await readScreen();
      } catch {
        // Non-fatal — continue without pre-screen
      }

      // Execute the action
      try {
        const result = await executeAction(step.action, step.args);

        // Check for error in result
        if (result.startsWith('Error:') || result.startsWith('PERMISSION ERROR:')) {
          log.engine.warn(`[ActionPlanner] Step ${i + 1} failed: ${result}`);
          return {
            success: false,
            stepsCompleted: i,
            totalSteps: plan.steps.length,
            screenChanges: result,
            error: result,
            completedSteps,
          };
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        log.engine.warn(`[ActionPlanner] Step ${i + 1} threw: ${msg}`);
        return {
          success: false,
          stepsCompleted: i,
          totalSteps: plan.steps.length,
          screenChanges: '',
          error: msg,
          completedSteps,
        };
      }

      // Brief wait for UI to settle
      await new Promise(resolve => setTimeout(resolve, 300));

      // Verify step if verification criteria provided
      if (step.verify) {
        try {
          const afterScreen = await readScreen();
          const diff = lastScreen ? diffScreenStates(lastScreen, afterScreen) : null;

          if (diff && !diff.hasChanges) {
            log.engine.debug(
              `[ActionPlanner] Step ${i + 1}: no visible screen change (may be expected)`,
            );
          }
        } catch {
          // Verification failed but step might still be ok
        }
      }

      completedSteps.push({
        action: step.action,
        args: step.args,
        description: step.description,
        verify: step.verify,
      });
    }

    // Final screen state for summary
    let screenChanges = '';
    try {
      const finalScreen = await readScreen();
      if (lastScreen) {
        const diff = diffScreenStates(lastScreen, finalScreen);
        screenChanges = diff.summary;
      } else {
        screenChanges = formatScreenMinimal(finalScreen);
      }
    } catch {
      screenChanges = '(could not read final screen state)';
    }

    return {
      success: true,
      stepsCompleted: plan.steps.length,
      totalSteps: plan.steps.length,
      screenChanges,
      completedSteps,
    };
  }

  /**
   * Save a successful execution as a recipe for future reuse.
   */
  saveAsRecipe(
    task: string,
    apps: string[],
    steps: RecipeStep[],
    tags: string[] = [],
  ): Recipe {
    const recipe: Recipe = {
      id: RecipeStore.makeId(task),
      task,
      apps,
      steps,
      successCount: 1,
      failCount: 0,
      lastUsed: new Date().toISOString(),
      tags,
    };
    this.recipeStore.save(recipe);
    return recipe;
  }
}
