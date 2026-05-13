/**
 * StackOwl — Python Static Analyzer
 *
 * Scans synthesized Python tool code for dangerous patterns before
 * writing to disk or executing. Provides a simple allowlist/denylist
 * approach: any match in FORBIDDEN means the code is rejected.
 */

const FORBIDDEN: Array<{ name: string; pattern: RegExp }> = [
  { name: "subprocess",  pattern: /\bsubprocess\b/u },
  { name: "os.system",   pattern: /\bos\.system\s*\(/u },
  { name: "eval",        pattern: /\beval\s*\(/u },
  { name: "exec",        pattern: /\bexec\s*\(/u },
  { name: "__import__",  pattern: /\b__import__\s*\(/u },
];

export interface PythonAnalysisResult {
  safe: boolean;
  patterns: string[];
}

export class PythonAnalyzer {
  static analyze(code: string): PythonAnalysisResult {
    const found = FORBIDDEN
      .filter(({ pattern }) => pattern.test(code))
      .map(({ name }) => name);
    return { safe: found.length === 0, patterns: found };
  }
}
