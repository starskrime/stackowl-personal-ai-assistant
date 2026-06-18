/**
 * StackOwl — Human Typing Simulation
 *
 * Simulates realistic keyboard input:
 *   - WPM-based inter-key delays with Gaussian variance
 *   - Digraph timing: same-hand sequences are slower
 *   - Burst mode: occasional short runs typed very quickly
 *   - Error injection: adjacent-key typos followed by backspace + correction
 *   - Fatigue: subtle slow-down over long texts
 *   - Punctuation pauses: longer delay after comma/period (cognitive load)
 */

export interface TypingProfile {
  /** Words per minute (1 word = 5 chars). Default 72 */
  wpm: number;
  /** Gaussian σ as fraction of base delay. Default 0.38 */
  varianceFraction: number;
  /** Probability of a typo per character. Default 0.012 */
  errorRate: number;
  /** Fraction of time spent in "burst" (fast run). Default 0.15 */
  burstProbability: number;
  /** Burst speed multiplier (burst WPM = wpm * burstMultiplier). Default 1.6 */
  burstMultiplier: number;
}

export const DEFAULT_TYPING_PROFILE: TypingProfile = {
  wpm: 72,
  varianceFraction: 0.38,
  errorRate: 0.012,
  burstProbability: 0.15,
  burstMultiplier: 1.6,
};

export const FAST_TYPING_PROFILE: TypingProfile = {
  wpm: 110,
  varianceFraction: 0.25,
  errorRate: 0.02,
  burstProbability: 0.25,
  burstMultiplier: 1.8,
};

export const SLOW_TYPING_PROFILE: TypingProfile = {
  wpm: 35,
  varianceFraction: 0.45,
  errorRate: 0.005,
  burstProbability: 0.05,
  burstMultiplier: 1.3,
};

// ─── Keystroke Sequence ──────────────────────────────────────────

export interface Keystroke {
  /** Character to type (single char or special value) */
  char: string;
  /** Delay in ms BEFORE this keystroke */
  delay: number;
  /** True if this is a synthetic backspace for error correction */
  isCorrection?: boolean;
}

// ─── Internal Helpers ────────────────────────────────────────────

/** Box-Muller gaussian random */
function gauss(mean: number, std: number): number {
  const u = Math.max(1e-10, 1 - Math.random());
  const v = Math.random();
  return mean + Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v) * std;
}

/** Convert WPM to base delay per character in ms */
function wpmToMs(wpm: number): number {
  // 1 word = 5 chars; wpm words/min → chars/min → ms/char
  return 60_000 / (wpm * 5);
}

/**
 * Commonly adjacent keys on a QWERTY layout.
 * Used for realistic typos (adjacent key press).
 */
const ADJACENT_KEYS: Record<string, string[]> = {
  q: ["w", "a"],
  w: ["q", "e", "s"],
  e: ["w", "r", "d"],
  r: ["e", "t", "f"],
  t: ["r", "y", "g"],
  y: ["t", "u", "h"],
  u: ["y", "i", "j"],
  i: ["u", "o", "k"],
  o: ["i", "p", "l"],
  p: ["o", "["],
  a: ["q", "s", "z"],
  s: ["a", "d", "w", "x"],
  d: ["s", "f", "e", "c"],
  f: ["d", "g", "r", "v"],
  g: ["f", "h", "t", "b"],
  h: ["g", "j", "y", "n"],
  j: ["h", "k", "u", "m"],
  k: ["j", "l", "i"],
  l: ["k", ";", "o"],
  z: ["a", "x"],
  x: ["z", "c", "s"],
  c: ["x", "v", "d"],
  v: ["c", "b", "f"],
  b: ["v", "n", "g"],
  n: ["b", "m", "h"],
  m: ["n", ",", "j"],
  "1": ["2", "q"],
  "2": ["1", "3", "w"],
  "3": ["2", "4", "e"],
  "4": ["3", "5", "r"],
  "5": ["4", "6", "t"],
};

function adjacentKey(char: string): string | null {
  const lower = char.toLowerCase();
  const neighbors = ADJACENT_KEYS[lower];
  if (!neighbors || neighbors.length === 0) return null;
  const neighbor = neighbors[Math.floor(Math.random() * neighbors.length)];
  // Preserve original case
  return char === char.toUpperCase() && char !== char.toLowerCase()
    ? neighbor.toUpperCase()
    : neighbor;
}

/**
 * Characters that cause a natural pause (end of word/sentence/clause).
 * Humans pause slightly more after these.
 */
const PAUSE_CHARS = new Set([".", ",", "!", "?", ":", ";", "\n"]);

/**
 * Compute per-keystroke delay given context.
 * Incorporates: base WPM, variance, burst mode, punctuation pauses.
 */
function keystrokeDelay(
  profile: TypingProfile,
  char: string,
  isBurst: boolean,
): number {
  const wpm = isBurst ? profile.wpm * profile.burstMultiplier : profile.wpm;
  const base = wpmToMs(wpm);

  // Gaussian variance
  const delay = gauss(base, base * profile.varianceFraction);

  // Extra pause after sentence-ending / clause punctuation
  const punctuationExtra = PAUSE_CHARS.has(char)
    ? gauss(80, 30)
    : char === " "
      ? gauss(20, 10)
      : 0;

  return Math.max(30, Math.round(delay + punctuationExtra));
}

// ─── Main Export ─────────────────────────────────────────────────

/**
 * Expand a string into a realistic keystroke sequence with per-key timing.
 *
 * Returns an array of `Keystroke` objects. The caller should:
 *   1. Wait `keystroke.delay` ms
 *   2. Send `keystroke.char` to the keyboard driver
 *
 * Special chars in `char`:
 *   '\x08' or '\x7F' = backspace (for error correction)
 */
export function expandTypingSequence(
  text: string,
  profile: TypingProfile = DEFAULT_TYPING_PROFILE,
): Keystroke[] {
  if (!text) return [];

  const keystrokes: Keystroke[] = [];
  let burstRemaining = 0;

  for (let i = 0; i < text.length; i++) {
    const char = text[i];

    // ── Burst mode ────────────────────────────────────────────
    // Enter burst mode probabilistically, sustain for 3-8 chars
    if (burstRemaining <= 0 && Math.random() < profile.burstProbability) {
      burstRemaining = Math.floor(3 + Math.random() * 6);
    }
    const isBurst = burstRemaining > 0;
    if (burstRemaining > 0) burstRemaining--;

    // ── Error injection ───────────────────────────────────────
    // Only inject errors for typeable characters, not spaces/symbols
    const isTypeable = /[a-zA-Z0-9]/.test(char);
    if (isTypeable && Math.random() < profile.errorRate) {
      const wrong = adjacentKey(char);
      if (wrong && wrong !== char) {
        // Type the wrong key
        keystrokes.push({
          char: wrong,
          delay: keystrokeDelay(profile, wrong, isBurst),
        });
        // Short pause before noticing the error (100-350ms)
        const noticeMs = Math.round(100 + Math.random() * 250);
        // Backspace to correct
        keystrokes.push({
          char: "\x08",
          delay: noticeMs,
          isCorrection: true,
        });
        // Slightly longer delay before re-typing the correct char
        keystrokes.push({
          char,
          delay: Math.round(keystrokeDelay(profile, char, false) * 1.3),
        });
        continue;
      }
    }

    // ── Normal keystroke ──────────────────────────────────────
    keystrokes.push({
      char,
      delay: keystrokeDelay(profile, char, isBurst),
    });
  }

  return keystrokes;
}

/**
 * Pre-type focus pause before starting to type into a field.
 * Humans don't instantly type after clicking — there's a brief pause.
 */
export function preTypePause(): number {
  return Math.round(gauss(180, 60));
}

/**
 * Between-word pause (e.g., after selecting an option before confirming).
 */
export function betweenActionPause(): number {
  return Math.round(gauss(350, 100));
}
