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
  { name: "os.popen",    pattern: /\bos\.popen\s*\(/u },
  { name: "os.execv",    pattern: /\bos\.exec[vpe]+\s*\(/u },
  { name: "os.spawn",    pattern: /\bos\.spawn[lve]+\s*\(/u },
  { name: "ctypes",      pattern: /\bctypes\b/u },
];

export interface PythonAnalysisResult {
  safe: boolean;
  patterns: string[];
}

export class PythonAnalyzer {
  static analyze(code: string): PythonAnalysisResult {
    // Strip Python single-line comments to avoid false positives
    const codeNoComments = code
      .split("\n")
      .filter(line => !line.trimStart().startsWith("#"))
      .join("\n");

    const found = FORBIDDEN
      .filter(({ pattern }) => pattern.test(codeNoComments))
      .map(({ name }) => name);
    return { safe: found.length === 0, patterns: found };
  }
}
