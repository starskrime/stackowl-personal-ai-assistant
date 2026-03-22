/**
 * StackOwl — Self-Healer
 *
 * When a subsystem fails repeatedly, the self-healer:
 *   1. Always uses the Anthropic (Claude) provider for diagnosis
 *   2. Reads the relevant source files to understand the full context
 *   3. Asks Claude to diagnose the root cause and generate a fix
 *   4. Applies the fix (config changes, code patches, data cleanup)
 *   5. Retries the failed operation
 *
 * This is not retry logic — it's genuine self-repair. The AI reads its
 * own code, understands what went wrong, and fixes itself.
 */

import { readFile, writeFile, readdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import type { ModelProvider } from '../providers/base.js';
import type { ProviderRegistry } from '../providers/registry.js';
import { log } from '../logger.js';

// ─── Types ──────────────────────────────────────────────────────

export interface HealingContext {
  /** Which subsystem failed (e.g. "learning", "evolution", "pellet-dedup") */
  subsystem: string;
  /** The error that triggered healing */
  error: Error;
  /** The operation that failed */
  operation: string;
  /** Any additional context (config values, recent logs, etc.) */
  context?: string;
}

export interface HealingResult {
  healed: boolean;
  diagnosis: string;
  action: string;
  /** Files that were modified */
  filesChanged: string[];
}

interface DiagnosisResponse {
  rootCause: string;
  action: 'config_fix' | 'data_cleanup' | 'code_patch' | 'restart_needed' | 'external_dependency' | 'skip';
  explanation: string;
  fix?: {
    file: string;
    type: 'replace' | 'write_json' | 'delete_file';
    search?: string;
    replace?: string;
    content?: string;
  };
  configFix?: {
    key: string;
    value: unknown;
    reason: string;
  };
  dataCleanup?: {
    directory: string;
    pattern: string;
    reason: string;
  };
}

// ─── Self-Healer ────────────────────────────────────────────────

export class SelfHealer {
  private anthropicProvider: ModelProvider | null = null;
  private srcRoot: string;
  private workspacePath: string;
  private healingHistory: Array<{
    timestamp: string;
    subsystem: string;
    diagnosis: string;
    action: string;
    success: boolean;
  }> = [];

  constructor(
    private providerRegistry: ProviderRegistry,
    projectRoot: string,
    workspacePath: string,
  ) {
    this.srcRoot = join(projectRoot, 'src');
    this.workspacePath = workspacePath;
  }

  /**
   * Attempt to heal a failing subsystem.
   *
   * Always uses Anthropic — the smartest model available for code understanding.
   * Reads the actual source files, understands the architecture, and applies fixes.
   */
  async heal(ctx: HealingContext): Promise<HealingResult> {
    const startTime = Date.now();

    try {
      // Step 1: Get the Anthropic provider (always use Claude for healing)
      const provider = this.getAnthropicProvider();
      if (!provider) {
        log.evolution.warn(
          '[SelfHealer] Anthropic provider not available — cannot self-heal. ' +
          'Add an Anthropic provider to stackowl.config.json to enable self-healing.',
        );
        return {
          healed: false,
          diagnosis: 'Anthropic provider not configured',
          action: 'none',
          filesChanged: [],
        };
      }

      log.evolution.evolve(
        `[SelfHealer] Healing ${ctx.subsystem}/${ctx.operation} — reading source files...`,
      );

      // Step 2: Gather the relevant source code for the failing subsystem
      const sourceContext = await this.gatherSourceContext(ctx.subsystem);

      // Step 3: Gather runtime context (config, workspace state, logs)
      const runtimeContext = await this.gatherRuntimeContext(ctx);

      // Step 4: Ask Claude to diagnose and prescribe a fix
      const diagnosis = await this.diagnose(provider, ctx, sourceContext, runtimeContext);

      log.evolution.evolve(
        `[SelfHealer] Diagnosis: ${diagnosis.rootCause} → action: ${diagnosis.action}`,
      );

      // Step 5: Apply the fix
      const result = await this.applyFix(diagnosis, ctx);

      const elapsed = Date.now() - startTime;
      log.evolution.evolve(
        `[SelfHealer] ${result.healed ? 'Healed' : 'Could not heal'} ` +
        `${ctx.subsystem}/${ctx.operation} in ${elapsed}ms — ${result.action}`,
      );

      // Record in history
      this.healingHistory.push({
        timestamp: new Date().toISOString(),
        subsystem: ctx.subsystem,
        diagnosis: diagnosis.rootCause,
        action: result.action,
        success: result.healed,
      });

      return result;
    } catch (err) {
      log.evolution.error(
        `[SelfHealer] Healing itself failed: ${err instanceof Error ? err.message : String(err)}`,
      );
      return {
        healed: false,
        diagnosis: `Healing failed: ${err instanceof Error ? err.message : String(err)}`,
        action: 'none',
        filesChanged: [],
      };
    }
  }

  /**
   * Get healing history for diagnostics.
   */
  getHistory(): typeof this.healingHistory {
    return [...this.healingHistory];
  }

  // ─── Private: Provider ────────────────────────────────────────

  private getAnthropicProvider(): ModelProvider | null {
    if (this.anthropicProvider) return this.anthropicProvider;

    try {
      this.anthropicProvider = this.providerRegistry.get('anthropic');
      return this.anthropicProvider;
    } catch {
      // Anthropic not registered — try to find any provider with "anthropic" or "claude" in name
      const providers = this.providerRegistry.listProviders();
      for (const name of providers) {
        if (name.toLowerCase().includes('anthropic') || name.toLowerCase().includes('claude')) {
          try {
            this.anthropicProvider = this.providerRegistry.get(name);
            return this.anthropicProvider;
          } catch { /* continue */ }
        }
      }
      return null;
    }
  }

  // ─── Private: Source Context Gathering ─────────────────────────

  /**
   * Read the relevant source files for a subsystem.
   * Maps subsystem names to the files that matter.
   */
  private async gatherSourceContext(subsystem: string): Promise<string> {
    const fileMap: Record<string, string[]> = {
      learning: [
        'learning/self-study.ts',
        'learning/extractor.ts',
        'learning/researcher.ts',
        'learning/knowledge-graph.ts',
        'learning/micro-learner.ts',
        'pellets/store.ts',
        'pellets/dedup.ts',
      ],
      evolution: [
        'owls/evolution.ts',
        'evolution/handler.ts',
        'evolution/detector.ts',
        'evolution/synthesizer.ts',
      ],
      pellets: [
        'pellets/store.ts',
        'pellets/dedup.ts',
        'pellets/tfidf.ts',
        'pellets/graph.ts',
        'pellets/concepts.ts',
      ],
      engine: [
        'engine/runtime.ts',
        'engine/router.ts',
        'engine/planner.ts',
      ],
      gateway: [
        'gateway/core.ts',
        'gateway/types.ts',
        'orchestrator/orchestrator.ts',
        'orchestrator/classifier.ts',
      ],
      tools: [
        'tools/search.ts',
        'tools/shell.ts',
        'tools/web.ts',
      ],
    };

    // Get the relevant files, or default to the subsystem directory
    const files = fileMap[subsystem] ?? [];
    const parts: string[] = [];

    for (const file of files) {
      const fullPath = join(this.srcRoot, file);
      if (!existsSync(fullPath)) continue;

      try {
        const content = await readFile(fullPath, 'utf-8');
        // Cap each file at 3000 chars to stay within context limits
        const trimmed = content.length > 3000
          ? content.slice(0, 3000) + '\n... [truncated]'
          : content;
        parts.push(`=== FILE: src/${file} ===\n${trimmed}\n`);
      } catch {
        // Skip unreadable files
      }
    }

    // If no mapped files, try to read the subsystem directory
    if (parts.length === 0) {
      const dirPath = join(this.srcRoot, subsystem);
      if (existsSync(dirPath)) {
        try {
          const dirFiles = await readdir(dirPath);
          for (const f of dirFiles.filter(f => f.endsWith('.ts')).slice(0, 5)) {
            const content = await readFile(join(dirPath, f), 'utf-8');
            const trimmed = content.length > 2000
              ? content.slice(0, 2000) + '\n... [truncated]'
              : content;
            parts.push(`=== FILE: src/${subsystem}/${f} ===\n${trimmed}\n`);
          }
        } catch { /* directory unreadable */ }
      }
    }

    return parts.join('\n');
  }

  /**
   * Gather runtime context: config, workspace state, recent errors.
   */
  private async gatherRuntimeContext(ctx: HealingContext): Promise<string> {
    const parts: string[] = [];

    // Config file
    const configPath = join(this.workspacePath, '..', 'stackowl.config.json');
    if (existsSync(configPath)) {
      try {
        const config = await readFile(configPath, 'utf-8');
        const parsed = JSON.parse(config);
        // Redact API keys
        const redacted = JSON.stringify(parsed, (key, value) => {
          if (key.toLowerCase().includes('key') || key.toLowerCase().includes('token')) {
            return typeof value === 'string' ? value.slice(0, 8) + '...[REDACTED]' : value;
          }
          return value;
        }, 2);
        parts.push(`=== CONFIG (keys redacted) ===\n${redacted.slice(0, 2000)}\n`);
      } catch { /* skip */ }
    }

    // Knowledge graph state
    const kgPath = join(this.workspacePath, 'knowledge_graph.json');
    if (existsSync(kgPath)) {
      try {
        const kg = await readFile(kgPath, 'utf-8');
        const parsed = JSON.parse(kg);
        const domainCount = Object.keys(parsed.domains ?? {}).length;
        const queueLen = (parsed.studyQueue ?? []).length;
        parts.push(`=== KNOWLEDGE GRAPH STATE ===\nDomains: ${domainCount}, Study queue: ${queueLen}\n`);
      } catch { /* skip */ }
    }

    // Pellet count
    const pelletsDir = join(this.workspacePath, 'pellets');
    if (existsSync(pelletsDir)) {
      try {
        const files = await readdir(pelletsDir);
        const mdFiles = files.filter(f => f.endsWith('.md'));
        parts.push(`=== PELLETS ===\n${mdFiles.length} pellet files on disk\n`);
      } catch { /* skip */ }
    }

    // Session log (last 50 lines)
    const logPath = join(this.workspacePath, 'logs', 'session.log');
    if (existsSync(logPath)) {
      try {
        const logContent = await readFile(logPath, 'utf-8');
        const lines = logContent.split('\n');
        const recentLines = lines.slice(-50).join('\n');
        parts.push(`=== RECENT SESSION LOG (last 50 lines) ===\n${recentLines}\n`);
      } catch { /* skip */ }
    }

    // Error details
    parts.push(
      `=== ERROR DETAILS ===\n` +
      `Subsystem: ${ctx.subsystem}\n` +
      `Operation: ${ctx.operation}\n` +
      `Error: ${ctx.error.message}\n` +
      `Stack: ${ctx.error.stack?.slice(0, 500) ?? 'no stack'}\n` +
      (ctx.context ? `Context: ${ctx.context}\n` : ''),
    );

    return parts.join('\n');
  }

  // ─── Private: Diagnosis ───────────────────────────────────────

  private async diagnose(
    provider: ModelProvider,
    _ctx: HealingContext,
    sourceContext: string,
    runtimeContext: string,
  ): Promise<DiagnosisResponse> {
    const prompt =
      `You are a self-healing AI system. A subsystem of the StackOwl AI assistant has failed.\n` +
      `Your job: read the source code, understand the architecture, diagnose the root cause, ` +
      `and prescribe a concrete fix.\n\n` +
      `SOURCE CODE:\n${sourceContext}\n\n` +
      `RUNTIME CONTEXT:\n${runtimeContext}\n\n` +
      `DIAGNOSE AND FIX:\n` +
      `1. Read and understand ALL the code above\n` +
      `2. Identify the root cause of the error\n` +
      `3. Determine the minimal fix\n\n` +
      `Return ONLY valid JSON:\n` +
      `{\n` +
      `  "rootCause": "one sentence explaining what's actually broken",\n` +
      `  "action": "config_fix|data_cleanup|code_patch|restart_needed|external_dependency|skip",\n` +
      `  "explanation": "detailed explanation of what went wrong and why your fix will work",\n` +
      `  "fix": {\n` +
      `    "file": "relative path from project root",\n` +
      `    "type": "replace|write_json|delete_file",\n` +
      `    "search": "exact string to find (for replace)",\n` +
      `    "replace": "replacement string (for replace)",\n` +
      `    "content": "full content (for write_json)"\n` +
      `  },\n` +
      `  "configFix": {\n` +
      `    "key": "dot-notation config path (e.g. pellets.dedup.similarityThreshold)",\n` +
      `    "value": "new value",\n` +
      `    "reason": "why this config change fixes it"\n` +
      `  },\n` +
      `  "dataCleanup": {\n` +
      `    "directory": "relative path",\n` +
      `    "pattern": "glob pattern of files to clean",\n` +
      `    "reason": "why these files need cleanup"\n` +
      `  }\n` +
      `}\n\n` +
      `RULES:\n` +
      `- Only include the fix type that applies (fix, configFix, or dataCleanup)\n` +
      `- For "skip": the error is expected/transient and no fix is needed\n` +
      `- For "external_dependency": the problem is outside our control (API down, network)\n` +
      `- For "restart_needed": the fix requires restarting the process\n` +
      `- For "config_fix": change a config value in stackowl.config.json\n` +
      `- For "data_cleanup": remove corrupted files\n` +
      `- For "code_patch": ONLY fix data files (JSON, markdown) — never patch .ts source\n` +
      `- Be conservative — prefer "skip" over risky changes\n` +
      `- Never delete pellet files unless they are clearly corrupted (not valid markdown)`;

    const response = await provider.chat(
      [
        {
          role: 'system',
          content:
            'You are a self-healing diagnostic AI. You read source code, diagnose failures, ' +
            'and prescribe minimal fixes. Output only valid JSON. Be conservative — ' +
            'a wrong fix is worse than no fix.',
        },
        { role: 'user', content: prompt },
      ],
      undefined,
      { temperature: 0.1, maxTokens: 2048 },
    );

    let jsonStr = response.content.trim();
    if (jsonStr.startsWith('```')) {
      jsonStr = jsonStr.replace(/^```json?/, '').replace(/```$/, '').trim();
    }
    // Strip JS-style comments and trailing commas
    jsonStr = jsonStr.replace(/\/\/[^\n]*/g, '');
    jsonStr = jsonStr.replace(/,\s*([}\]])/g, '$1');

    const jsonMatch = jsonStr.match(/\{[\s\S]*\}/);
    if (!jsonMatch) {
      throw new Error('Diagnosis returned no JSON');
    }

    return JSON.parse(jsonMatch[0]) as DiagnosisResponse;
  }

  // ─── Private: Apply Fix ───────────────────────────────────────

  private async applyFix(
    diagnosis: DiagnosisResponse,
    _ctx: HealingContext,
  ): Promise<HealingResult> {
    const base: HealingResult = {
      healed: false,
      diagnosis: diagnosis.rootCause,
      action: diagnosis.action,
      filesChanged: [],
    };

    switch (diagnosis.action) {
      case 'skip':
        base.healed = true; // Nothing to fix
        base.action = `Transient: ${diagnosis.explanation}`;
        return base;

      case 'external_dependency':
        base.action = `External: ${diagnosis.explanation}`;
        return base;

      case 'restart_needed':
        base.action = `Restart required: ${diagnosis.explanation}`;
        log.evolution.warn(
          `[SelfHealer] Fix requires restart: ${diagnosis.explanation}`,
        );
        return base;

      case 'config_fix':
        return this.applyConfigFix(diagnosis, base);

      case 'data_cleanup':
        return this.applyDataCleanup(diagnosis, base);

      case 'code_patch':
        return this.applyDataPatch(diagnosis, base);

      default:
        base.action = `Unknown action: ${diagnosis.action}`;
        return base;
    }
  }

  private async applyConfigFix(
    diagnosis: DiagnosisResponse,
    result: HealingResult,
  ): Promise<HealingResult> {
    if (!diagnosis.configFix) return result;

    const configPath = join(this.workspacePath, '..', 'stackowl.config.json');
    if (!existsSync(configPath)) {
      result.action = 'Config file not found';
      return result;
    }

    try {
      const raw = await readFile(configPath, 'utf-8');
      const config = JSON.parse(raw);

      // Apply dot-notation path
      const keys = diagnosis.configFix.key.split('.');
      let obj = config;
      for (let i = 0; i < keys.length - 1; i++) {
        if (typeof obj[keys[i]] !== 'object' || obj[keys[i]] === null) {
          obj[keys[i]] = {};
        }
        obj = obj[keys[i]];
      }
      obj[keys[keys.length - 1]] = diagnosis.configFix.value;

      await writeFile(configPath, JSON.stringify(config, null, 2), 'utf-8');

      result.healed = true;
      result.action = `Config: set ${diagnosis.configFix.key} = ${JSON.stringify(diagnosis.configFix.value)} — ${diagnosis.configFix.reason}`;
      result.filesChanged = [configPath];

      log.evolution.evolve(
        `[SelfHealer] Applied config fix: ${diagnosis.configFix.key} = ${JSON.stringify(diagnosis.configFix.value)}`,
      );
    } catch (err) {
      result.action = `Config fix failed: ${err instanceof Error ? err.message : String(err)}`;
    }

    return result;
  }

  private async applyDataCleanup(
    diagnosis: DiagnosisResponse,
    result: HealingResult,
  ): Promise<HealingResult> {
    if (!diagnosis.dataCleanup) return result;

    const dir = join(this.workspacePath, diagnosis.dataCleanup.directory);
    if (!existsSync(dir)) {
      result.action = `Cleanup dir not found: ${diagnosis.dataCleanup.directory}`;
      return result;
    }

    try {
      const { unlink } = await import('node:fs/promises');
      const files = await readdir(dir);
      const pattern = new RegExp(
        diagnosis.dataCleanup.pattern
          .replace(/\./g, '\\.')
          .replace(/\*/g, '.*')
          .replace(/\?/g, '.'),
      );

      let cleaned = 0;
      for (const file of files) {
        if (pattern.test(file)) {
          await unlink(join(dir, file));
          cleaned++;
          result.filesChanged.push(join(dir, file));
        }
      }

      result.healed = cleaned > 0;
      result.action = `Cleaned ${cleaned} file(s) from ${diagnosis.dataCleanup.directory} — ${diagnosis.dataCleanup.reason}`;

      log.evolution.evolve(
        `[SelfHealer] Data cleanup: removed ${cleaned} file(s) from ${diagnosis.dataCleanup.directory}`,
      );
    } catch (err) {
      result.action = `Cleanup failed: ${err instanceof Error ? err.message : String(err)}`;
    }

    return result;
  }

  private async applyDataPatch(
    diagnosis: DiagnosisResponse,
    result: HealingResult,
  ): Promise<HealingResult> {
    if (!diagnosis.fix) return result;

    // Safety: only allow patching data files (JSON, MD), never TypeScript source
    const allowedExtensions = ['.json', '.md', '.yaml', '.yml', '.txt'];
    const ext = diagnosis.fix.file.slice(diagnosis.fix.file.lastIndexOf('.'));
    if (!allowedExtensions.includes(ext)) {
      result.action = `Refused to patch ${diagnosis.fix.file} — only data files allowed (${allowedExtensions.join(', ')})`;
      log.evolution.warn(
        `[SelfHealer] Blocked attempt to patch source file: ${diagnosis.fix.file}`,
      );
      return result;
    }

    const filePath = join(this.workspacePath, '..', diagnosis.fix.file);

    try {
      switch (diagnosis.fix.type) {
        case 'write_json': {
          if (!diagnosis.fix.content) break;
          await writeFile(filePath, diagnosis.fix.content, 'utf-8');
          result.healed = true;
          result.action = `Wrote ${diagnosis.fix.file}`;
          result.filesChanged = [filePath];
          break;
        }

        case 'replace': {
          if (!diagnosis.fix.search || diagnosis.fix.replace === undefined) break;
          if (!existsSync(filePath)) break;
          const content = await readFile(filePath, 'utf-8');
          if (!content.includes(diagnosis.fix.search)) {
            result.action = `Search string not found in ${diagnosis.fix.file}`;
            break;
          }
          const newContent = content.replace(diagnosis.fix.search, diagnosis.fix.replace);
          await writeFile(filePath, newContent, 'utf-8');
          result.healed = true;
          result.action = `Patched ${diagnosis.fix.file}`;
          result.filesChanged = [filePath];
          break;
        }

        case 'delete_file': {
          if (!existsSync(filePath)) break;
          const { unlink } = await import('node:fs/promises');
          await unlink(filePath);
          result.healed = true;
          result.action = `Deleted ${diagnosis.fix.file}`;
          result.filesChanged = [filePath];
          break;
        }
      }

      if (result.healed) {
        log.evolution.evolve(
          `[SelfHealer] Applied data patch: ${result.action}`,
        );
      }
    } catch (err) {
      result.action = `Patch failed: ${err instanceof Error ? err.message : String(err)}`;
    }

    return result;
  }
}
