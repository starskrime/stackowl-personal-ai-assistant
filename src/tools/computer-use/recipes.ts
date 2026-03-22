/**
 * StackOwl — Workflow Recipes
 *
 * Records successful multi-step computer_use action sequences and replays
 * them on similar future tasks. Recipes are stored as markdown files in
 * workspace/recipes/ with YAML frontmatter for searchability.
 *
 * No extra models required — uses the same configured provider for
 * matching and the existing BM25 TF-IDF for fast candidate lookup.
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { log } from '../../logger.js';

// ─── Types ───────────────────────────────────────────────────────────────────

export interface RecipeStep {
  action: string;
  args: Record<string, unknown>;
  /** What to verify after this step (element to look for, expected app state, etc.) */
  verify?: string;
  /** Description of what this step does */
  description: string;
}

export interface Recipe {
  id: string;
  /** Natural-language description of the task this recipe accomplishes */
  task: string;
  /** Application(s) involved */
  apps: string[];
  /** Ordered steps */
  steps: RecipeStep[];
  /** How many times this recipe was successfully used */
  successCount: number;
  /** How many times this recipe failed */
  failCount: number;
  /** ISO timestamp of last successful use */
  lastUsed?: string;
  /** Tags for searchability */
  tags: string[];
}

// ─── Recipe Store ────────────────────────────────────────────────────────────

export class RecipeStore {
  private recipesDir: string;
  private recipes: Map<string, Recipe> = new Map();

  constructor(workspacePath: string) {
    this.recipesDir = join(workspacePath, 'recipes');
    if (!existsSync(this.recipesDir)) {
      mkdirSync(this.recipesDir, { recursive: true });
    }
  }

  /** Load all recipes from disk */
  async init(): Promise<void> {
    if (!existsSync(this.recipesDir)) return;

    const files = readdirSync(this.recipesDir).filter(f => f.endsWith('.json'));
    for (const file of files) {
      try {
        const raw = readFileSync(join(this.recipesDir, file), 'utf-8');
        const recipe = JSON.parse(raw) as Recipe;
        this.recipes.set(recipe.id, recipe);
      } catch {
        // Skip corrupted files
      }
    }
    log.engine.info(`[RecipeStore] Loaded ${this.recipes.size} recipes`);
  }

  /** Save a new recipe */
  save(recipe: Recipe): void {
    this.recipes.set(recipe.id, recipe);
    const path = join(this.recipesDir, `${recipe.id}.json`);
    writeFileSync(path, JSON.stringify(recipe, null, 2), 'utf-8');
    log.engine.info(`[RecipeStore] Saved recipe: "${recipe.task}" (${recipe.steps.length} steps)`);
  }

  /** Update an existing recipe (bump success/fail counts, lastUsed) */
  update(id: string, updates: Partial<Recipe>): void {
    const existing = this.recipes.get(id);
    if (!existing) return;
    const updated = { ...existing, ...updates };
    this.recipes.set(id, updated);
    const path = join(this.recipesDir, `${id}.json`);
    writeFileSync(path, JSON.stringify(updated, null, 2), 'utf-8');
  }

  /** Get a recipe by ID */
  get(id: string): Recipe | undefined {
    return this.recipes.get(id);
  }

  /** List all recipes */
  listAll(): Recipe[] {
    return Array.from(this.recipes.values());
  }

  /**
   * Find recipes matching a task description.
   * Uses simple word overlap scoring — no extra model calls needed.
   */
  findMatching(taskDescription: string, maxResults = 3): { recipe: Recipe; score: number }[] {
    const queryWords = new Set(
      taskDescription.toLowerCase().split(/\s+/).filter(w => w.length > 2),
    );
    if (queryWords.size === 0) return [];

    const scored: { recipe: Recipe; score: number }[] = [];

    for (const recipe of this.recipes.values()) {
      const recipeWords = new Set(
        `${recipe.task} ${recipe.tags.join(' ')} ${recipe.apps.join(' ')}`
          .toLowerCase()
          .split(/\s+/)
          .filter(w => w.length > 2),
      );

      // Jaccard similarity
      let intersection = 0;
      for (const w of queryWords) {
        if (recipeWords.has(w)) intersection++;
      }
      const union = new Set([...queryWords, ...recipeWords]).size;
      const score = union > 0 ? intersection / union : 0;

      // Boost score by success rate
      const totalUses = recipe.successCount + recipe.failCount;
      const reliability = totalUses > 0 ? recipe.successCount / totalUses : 0.5;
      const boostedScore = score * (0.7 + 0.3 * reliability);

      if (boostedScore > 0.15) {
        scored.push({ recipe, score: boostedScore });
      }
    }

    return scored
      .sort((a, b) => b.score - a.score)
      .slice(0, maxResults);
  }

  /** Generate a recipe ID from a task description */
  static makeId(task: string): string {
    return task
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-|-$/g, '')
      .slice(0, 60) + '-' + Date.now().toString(36);
  }
}
