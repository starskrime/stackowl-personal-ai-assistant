/**
 * StackOwl — Environment Scanner
 *
 * Runs a fast (< 500ms) environment scan before task decomposition so
 * the TaskDecomposer operates on real environment facts rather than guesses.
 *
 * Detects:
 *   - Primary programming language (from file extensions + config files)
 *   - Frameworks / runtimes (package.json deps, Cargo.toml, etc.)
 *   - Project structure (top-level dirs + key config files)
 *   - Available tooling (git, docker, make, npm, etc.)
 *
 * Output is a compact text block injected into the decomposition prompt.
 *
 * Architecture: pure async scanner, no side effects, no LLM calls.
 * Gracefully degrades — any step that fails is skipped silently.
 */

import { readdir, readFile } from "node:fs/promises";
import { join, extname } from "node:path";
import { log } from "../logger.js";

// ─── Types ────────────────────────────────────────────────────────

export interface EnvSnapshot {
  cwd: string;
  language: string;
  frameworks: string[];
  configFiles: string[];
  topLevelDirs: string[];
  tooling: string[];
  /** Compact text block ready for LLM injection */
  summary: string;
}

// ─── Config detection patterns ────────────────────────────────────

const CONFIG_PATTERNS: Record<string, { lang: string; frameworks: string[] }> = {
  "package.json":    { lang: "TypeScript/JavaScript", frameworks: [] },
  "tsconfig.json":   { lang: "TypeScript", frameworks: [] },
  "Cargo.toml":      { lang: "Rust", frameworks: [] },
  "go.mod":          { lang: "Go", frameworks: [] },
  "pyproject.toml":  { lang: "Python", frameworks: [] },
  "requirements.txt":{ lang: "Python", frameworks: [] },
  "Gemfile":         { lang: "Ruby", frameworks: [] },
  "pom.xml":         { lang: "Java", frameworks: ["Maven"] },
  "build.gradle":    { lang: "Kotlin/Java", frameworks: ["Gradle"] },
  "CMakeLists.txt":  { lang: "C/C++", frameworks: ["CMake"] },
  "Makefile":        { lang: "C/C++", frameworks: ["Make"] },
  "composer.json":   { lang: "PHP", frameworks: [] },
};

const FRAMEWORK_PATTERNS: Record<string, string[]> = {
  "next.config":     ["Next.js"],
  "nuxt.config":     ["Nuxt.js"],
  "vite.config":     ["Vite"],
  "svelte.config":   ["SvelteKit"],
  "astro.config":    ["Astro"],
  "remix.config":    ["Remix"],
  "tailwind.config": ["Tailwind CSS"],
  "prisma":          ["Prisma ORM"],
  "drizzle.config":  ["Drizzle ORM"],
  "docker-compose":  ["Docker Compose"],
  "Dockerfile":      ["Docker"],
  ".github":         ["GitHub Actions"],
  "terraform":       ["Terraform"],
};

const TOOLING_CHECKS = [
  { cmd: "git", dir: ".git" },
  { cmd: "docker", dir: "Dockerfile" },
  { cmd: "make", dir: "Makefile" },
];

// ─── EnvironmentScanner ───────────────────────────────────────────

export class EnvironmentScanner {

  /**
   * Scan the given directory (defaults to process.cwd()).
   * Never throws — returns a partial result if anything fails.
   */
  async scan(cwd?: string): Promise<EnvSnapshot> {
    const dir = cwd ?? process.cwd();

    const [entries, pkgJson] = await Promise.all([
      this.listTop(dir),
      this.readPackageJson(dir),
    ]);

    const { language, configFiles, frameworks: cfgFrameworks } = this.detectFromEntries(entries);
    const pkgFrameworks = this.detectFromPackageJson(pkgJson);
    const dirFrameworks = this.detectFromDirNames(entries);
    const tooling = this.detectTooling(entries);

    const frameworks = [
      ...new Set([...cfgFrameworks, ...pkgFrameworks, ...dirFrameworks]),
    ].slice(0, 6);

    const topLevelDirs = entries
      .filter((e) => e.isDir)
      .map((e) => e.name)
      .filter((n) => !n.startsWith(".") && n !== "node_modules" && n !== "dist")
      .slice(0, 8);

    const snapshot: EnvSnapshot = {
      cwd: dir,
      language,
      frameworks,
      configFiles,
      topLevelDirs,
      tooling,
      summary: "",
    };

    snapshot.summary = this.buildSummary(snapshot);
    log.engine.debug(`[EnvScanner] ${language}, frameworks: [${frameworks.join(", ")}], tooling: [${tooling.join(", ")}]`);

    return snapshot;
  }

  // ─── Private ─────────────────────────────────────────────────

  private async listTop(dir: string): Promise<Array<{ name: string; isDir: boolean }>> {
    try {
      const entries = await readdir(dir, { withFileTypes: true });
      return entries.map((e) => ({ name: e.name, isDir: e.isDirectory() }));
    } catch {
      return [];
    }
  }

  private async readPackageJson(dir: string): Promise<Record<string, unknown> | null> {
    try {
      const raw = await readFile(join(dir, "package.json"), "utf-8");
      return JSON.parse(raw) as Record<string, unknown>;
    } catch {
      return null;
    }
  }

  private detectFromEntries(entries: Array<{ name: string; isDir: boolean }>): {
    language: string;
    configFiles: string[];
    frameworks: string[];
  } {
    const names = new Set(entries.map((e) => e.name));
    let language = "Unknown";
    const configFiles: string[] = [];
    const frameworks: string[] = [];

    for (const [file, info] of Object.entries(CONFIG_PATTERNS)) {
      if (names.has(file)) {
        configFiles.push(file);
        if (language === "Unknown") language = info.lang;
        frameworks.push(...info.frameworks);
      }
    }

    // Check framework patterns
    for (const entry of entries) {
      for (const [pattern, fws] of Object.entries(FRAMEWORK_PATTERNS)) {
        if (entry.name.startsWith(pattern) || entry.name === pattern) {
          frameworks.push(...fws);
        }
      }
    }

    // Infer language from file extensions if still unknown
    if (language === "Unknown") {
      const extCounts: Record<string, number> = {};
      for (const entry of entries.filter((e) => !e.isDir)) {
        const ext = extname(entry.name).toLowerCase();
        if (ext) extCounts[ext] = (extCounts[ext] ?? 0) + 1;
      }
      const dominant = Object.entries(extCounts).sort((a, b) => b[1] - a[1])[0];
      if (dominant) {
        const langMap: Record<string, string> = {
          ".ts": "TypeScript", ".tsx": "TypeScript (React)",
          ".js": "JavaScript", ".jsx": "JavaScript (React)",
          ".py": "Python", ".rs": "Rust", ".go": "Go",
          ".rb": "Ruby", ".java": "Java", ".kt": "Kotlin",
          ".cs": "C#", ".cpp": "C++", ".c": "C",
          ".swift": "Swift", ".php": "PHP",
        };
        language = langMap[dominant[0]] ?? `${dominant[0]} files`;
      }
    }

    return { language, configFiles: configFiles.slice(0, 6), frameworks };
  }

  private detectFromPackageJson(pkg: Record<string, unknown> | null): string[] {
    if (!pkg) return [];
    const allDeps = {
      ...((pkg.dependencies ?? {}) as Record<string, string>),
      ...((pkg.devDependencies ?? {}) as Record<string, string>),
    };

    const detected: string[] = [];
    const depNames = Object.keys(allDeps);

    const patterns: Array<[RegExp, string]> = [
      [/^react$/, "React"],
      [/^next$/, "Next.js"],
      [/^vue$/, "Vue.js"],
      [/^@angular\/core$/, "Angular"],
      [/^svelte$/, "Svelte"],
      [/^express$/, "Express"],
      [/^fastify$/, "Fastify"],
      [/^hono$/, "Hono"],
      [/^@nestjs\/core$/, "NestJS"],
      [/^prisma$/, "Prisma"],
      [/^drizzle-orm$/, "Drizzle"],
      [/^tailwindcss$/, "Tailwind CSS"],
      [/^vitest$/, "Vitest"],
      [/^jest$/, "Jest"],
      [/^grammy$/, "grammY (Telegram)"],
      [/^telegraf$/, "Telegraf (Telegram)"],
    ];

    for (const [re, name] of patterns) {
      if (depNames.some((d) => re.test(d))) detected.push(name);
    }

    return detected.slice(0, 6);
  }

  private detectFromDirNames(entries: Array<{ name: string; isDir: boolean }>): string[] {
    const dirNames = new Set(entries.filter((e) => e.isDir).map((e) => e.name));
    const detected: string[] = [];
    if (dirNames.has(".github")) detected.push("GitHub Actions");
    if (dirNames.has("terraform") || dirNames.has(".terraform")) detected.push("Terraform");
    if (dirNames.has("k8s") || dirNames.has("kubernetes")) detected.push("Kubernetes");
    return detected;
  }

  private detectTooling(entries: Array<{ name: string; isDir: boolean }>): string[] {
    const names = new Set(entries.map((e) => e.name));
    const tooling: string[] = [];
    for (const { cmd, dir } of TOOLING_CHECKS) {
      if (names.has(dir)) tooling.push(cmd);
    }
    if (names.has("package.json")) tooling.push("npm/pnpm/bun");
    if (names.has("Cargo.toml")) tooling.push("cargo");
    return tooling;
  }

  private buildSummary(s: EnvSnapshot): string {
    const lines: string[] = [
      `Project environment:`,
      `  Language: ${s.language}`,
    ];
    if (s.frameworks.length > 0) lines.push(`  Frameworks: ${s.frameworks.join(", ")}`);
    if (s.tooling.length > 0) lines.push(`  Tooling: ${s.tooling.join(", ")}`);
    if (s.configFiles.length > 0) lines.push(`  Config files: ${s.configFiles.join(", ")}`);
    if (s.topLevelDirs.length > 0) lines.push(`  Top-level dirs: ${s.topLevelDirs.join(", ")}`);
    return lines.join("\n");
  }
}
