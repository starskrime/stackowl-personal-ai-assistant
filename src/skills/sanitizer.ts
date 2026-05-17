/**
 * StackOwl — Skill Sanitizer
 *
 * Two-phase sanitization for skills installed from external vendors.
 *
 * Phase 1 (fast): deterministic find/replace of known vendor tokens in the
 *   instruction body. Never touches YAML frontmatter keys — those are parsed
 *   as structured metadata by SkillsRegistry.
 *
 * Phase 2 (flag): scan remaining body text for patterns that look like
 *   vendor-specific references and could not be auto-replaced. Returns them
 *   so the caller can show a review diff to the user.
 */

// ─── Types ────────────────────────────────────────────────────────────────────

export interface Replacement {
  from: string;
  to: string;
  line: number;
  count: number;
}

export interface FlaggedToken {
  token: string;
  line: number;
  context: string;
}

export interface SanitizationResult {
  /** Sanitized content, ready to write to disk. */
  content: string;
  /** Auto-replacements made in phase 1. */
  replacements: Replacement[];
  /** Tokens that look vendor-ish but were not auto-replaced. */
  flagged: FlaggedToken[];
  /** True when flagged is non-empty — caller should show a review diff. */
  needsReview: boolean;
}

// ─── Substitution table ───────────────────────────────────────────────────────
//
// Order matters: longer / more-specific patterns first.
// These are applied only to the instruction body (below the frontmatter block).

interface Substitution {
  pattern: RegExp;
  replacement: string;
  description: string;
}

const SUBSTITUTIONS: Substitution[] = [
  // Paths
  {
    pattern: /~\/\.claude\//g,
    replacement: "~/.stackowl/",
    description: "claude home dir → stackowl",
  },
  {
    pattern: /~\/\.clawdbot\//g,
    replacement: "~/.stackowl/",
    description: "clawdbot home dir → stackowl",
  },

  // CLI commands — word-boundary aware, only when used as a command token
  {
    pattern: /\bclawdbot(?=\s|$)/g,
    replacement: "stackowl",
    description: "clawdbot CLI command → stackowl",
  },

  // Config / documentation file references
  {
    pattern: /\bCLAUDE\.md\b/g,
    replacement: "SKILL.md",
    description: "CLAUDE.md → SKILL.md",
  },
  {
    pattern: /\bAGENTS\.md\b/g,
    replacement: "SKILL.md",
    description: "AGENTS.md → SKILL.md",
  },
];

// ─── Flag patterns ────────────────────────────────────────────────────────────
//
// Patterns that suggest a vendor-specific reference that phase 1 didn't handle.
// We flag but don't auto-replace — too risky to guess.

const FLAG_PATTERNS: { pattern: RegExp; label: string }[] = [
  { pattern: /clawhub\.ai/gi, label: "vendor URL" },
  { pattern: /anthropic\.com/gi, label: "vendor URL" },
  { pattern: /\bclaude-code\b/gi, label: "claude-code reference" },
  { pattern: /\bclaw\s+(?:install|run|search|skill)/gi, label: "claw subcommand" },
];

// ─── Frontmatter boundary ─────────────────────────────────────────────────────

/**
 * Split SKILL.md into frontmatter and body.
 * Frontmatter = lines between the first and second `---` delimiters.
 * Everything after is the body.
 */
function splitFrontmatter(content: string): { front: string; body: string } {
  const lines = content.split("\n");
  if (lines[0]?.trim() !== "---") {
    return { front: "", body: content };
  }

  const closeIdx = lines.findIndex((l, i) => i > 0 && l.trim() === "---");
  if (closeIdx === -1) {
    return { front: "", body: content };
  }

  const front = lines.slice(0, closeIdx + 1).join("\n");
  const body = lines.slice(closeIdx + 1).join("\n");
  return { front, body };
}

// ─── Core sanitize function ───────────────────────────────────────────────────

export function sanitize(content: string): SanitizationResult {
  const { front, body } = splitFrontmatter(content);

  const replacements: Replacement[] = [];
  const flagged: FlaggedToken[] = [];

  // Phase 1: deterministic substitutions on body only
  let sanitizedBody = body;

  for (const sub of SUBSTITUTIONS) {
    let match: RegExpExecArray | null;
    const lineHits = new Map<number, { count: number }>();

    // count hits per line (for reporting)
    const clone = new RegExp(sub.pattern.source, sub.pattern.flags);
    const bodyForScan = body;
    clone.lastIndex = 0;
    const lineStarts: number[] = [0];
    for (let i = 0; i < body.length; i++) {
      if (body[i] === "\n") lineStarts.push(i + 1);
    }

    while ((match = clone.exec(bodyForScan)) !== null) {
      const pos = match.index;
      // binary search for line number
      let lo = 0;
      let hi = lineStarts.length - 1;
      while (lo < hi) {
        const mid = (lo + hi + 1) >> 1;
        if (lineStarts[mid] <= pos) lo = mid;
        else hi = mid - 1;
      }
      const lineNo = lo + 1;
      const hit = lineHits.get(lineNo) ?? { count: 0 };
      hit.count++;
      lineHits.set(lineNo, hit);
    }

    const before = sanitizedBody;
    sanitizedBody = sanitizedBody.replace(sub.pattern, sub.replacement);

    if (sanitizedBody !== before) {
      for (const [line, { count }] of lineHits) {
        replacements.push({
          from: sub.description.split("→")[0].trim(),
          to: sub.replacement,
          line,
          count,
        });
      }
    }
  }

  // Phase 2: flag remaining vendor tokens
  const sanitizedLines = sanitizedBody.split("\n");
  for (let i = 0; i < sanitizedLines.length; i++) {
    const line = sanitizedLines[i];
    for (const { pattern, label } of FLAG_PATTERNS) {
      pattern.lastIndex = 0;
      if (pattern.test(line)) {
        pattern.lastIndex = 0;
        flagged.push({
          token: label,
          line: i + 1,
          context: line.trim().slice(0, 120),
        });
      }
    }
  }

  const finalContent = front ? `${front}\n${sanitizedBody}` : sanitizedBody;

  return {
    content: finalContent,
    replacements,
    flagged,
    needsReview: flagged.length > 0,
  };
}
