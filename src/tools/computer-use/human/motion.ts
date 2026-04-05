/**
 * StackOwl — Human Motion Profile
 *
 * Makes mouse movements look like a real person:
 *   - Cubic Bézier curves instead of teleportation or linear steps
 *   - Ease-in-out velocity (slow start, fast middle, slow end)
 *   - Gaussian micro-jitter on each waypoint
 *   - Natural target imprecision (humans don't click pixel-perfectly)
 *   - Fitts-law-based timing (longer distances → proportionally longer duration)
 *   - Occasional overshoot + correction micro-movement
 */

export interface Point {
  x: number;
  y: number;
}

export interface MotionOptions {
  /** Number of intermediate waypoints. Default: auto (distance-based, 10–60) */
  steps?: number;
  /** Gaussian σ for per-waypoint jitter in pixels. Default 1.5 */
  jitter?: number;
  /** Curve intensity 0–1 (0 = straight line). Default 0.28 */
  curveIntensity?: number;
  /** If true, add a small overshoot + correction at destination. Default false */
  overshoot?: boolean;
}

// ─── Math Utilities ──────────────────────────────────────────────

/**
 * Box-Muller transform — produces gaussian-distributed random number.
 */
function gaussianRandom(mean = 0, std = 1): number {
  // Avoid log(0) by clamping u to (0, 1)
  const u = Math.max(1e-10, 1 - Math.random());
  const v = Math.random();
  const z = Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  return mean + z * std;
}

/**
 * Ease-in-out cubic: slow→fast→slow over [0,1].
 * Derived from CSS cubic-bezier(0.42, 0, 0.58, 1).
 */
function easeInOutCubic(t: number): number {
  return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
}

/**
 * Evaluate cubic Bézier curve at parameter t ∈ [0,1].
 */
function bezier(p0: Point, p1: Point, p2: Point, p3: Point, t: number): Point {
  const mt = 1 - t;
  return {
    x:
      mt ** 3 * p0.x +
      3 * mt ** 2 * t * p1.x +
      3 * mt * t ** 2 * p2.x +
      t ** 3 * p3.x,
    y:
      mt ** 3 * p0.y +
      3 * mt ** 2 * t * p1.y +
      3 * mt * t ** 2 * p2.y +
      t ** 3 * p3.y,
  };
}

// ─── Main Exports ────────────────────────────────────────────────

/**
 * Generate a human-like cursor path from `from` to `to`.
 *
 * Returns an array of intermediate waypoints (not including `from`,
 * including `to`) that should be passed one-by-one to driver.mouseMove().
 */
export function generateHumanPath(
  from: Point,
  to: Point,
  opts: MotionOptions = {},
): Point[] {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const dist = Math.sqrt(dx * dx + dy * dy);

  // Very short distances: just move directly
  if (dist < 4) return [{ x: Math.round(to.x), y: Math.round(to.y) }];

  const steps =
    opts.steps ?? Math.max(12, Math.min(60, Math.round(dist / 9)));
  const intensity = opts.curveIntensity ?? 0.28;
  const jitter = opts.jitter ?? 1.5;

  // Perpendicular unit vector for curving off the straight path
  const perpX = -dy / dist;
  const perpY = dx / dist;

  // Gaussian amplitude for curve bulge (proportional to distance)
  const amp1 = gaussianRandom(0, dist * intensity * 0.45);
  const amp2 = gaussianRandom(0, dist * intensity * 0.25);

  // Control points offset from the interpolated straight line
  const cp1: Point = {
    x: from.x + dx * 0.3 + perpX * amp1,
    y: from.y + dy * 0.3 + perpY * amp1,
  };
  const cp2: Point = {
    x: from.x + dx * 0.7 + perpX * amp2,
    y: from.y + dy * 0.7 + perpY * amp2,
  };

  const points: Point[] = [];

  for (let i = 1; i <= steps; i++) {
    const t = easeInOutCubic(i / steps);
    const pt = bezier(from, cp1, cp2, to, t);
    const isLast = i === steps;

    // Add micro-jitter to all but the final destination
    const nx = isLast ? pt.x : pt.x + gaussianRandom(0, jitter);
    const ny = isLast ? pt.y : pt.y + gaussianRandom(0, jitter);
    points.push({ x: Math.round(nx), y: Math.round(ny) });
  }

  // Optional: tiny overshoot + correction on final approach
  if (opts.overshoot && Math.random() < 0.3) {
    const overshootX = to.x + gaussianRandom(0, 4);
    const overshootY = to.y + gaussianRandom(0, 4);
    // Insert overshoot before last point
    points.splice(points.length - 1, 0, {
      x: Math.round(overshootX),
      y: Math.round(overshootY),
    });
    // Final point: correct back to actual target
    points.push({ x: Math.round(to.x), y: Math.round(to.y) });
  }

  return points;
}

/**
 * Apply natural imprecision to a click target.
 *
 * Humans don't click pixel-perfectly — they aim slightly off.
 * σ=2 means 68% of clicks land within 2px of the target.
 */
export function humanizeTarget(target: Point, sigma = 2): Point {
  return {
    x: Math.round(target.x + gaussianRandom(0, sigma)),
    y: Math.round(target.y + gaussianRandom(0, sigma)),
  };
}

/**
 * Fitts-law-inspired movement duration (ms).
 *
 * Approximation: T = a + b·log₂(2D/W)
 * Typical human: 100-800ms across normal screen distances.
 */
export function humanMoveDuration(from: Point, to: Point): number {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const dist = Math.sqrt(dx * dx + dy * dy);

  if (dist < 5) return 30 + Math.random() * 40;

  // Base duration grows logarithmically with distance
  const base = 80 + Math.log2(dist + 1) * 38;

  // ±20% variance
  const variance = gaussianRandom(1, 0.2);
  return Math.round(base * Math.max(0.5, Math.min(2.0, variance)));
}

/**
 * Delay between mouse move steps (ms) given total duration and step count.
 * Returns an array of per-step delays with slight jitter so speed varies.
 */
export function stepDelays(durationMs: number, steps: number): number[] {
  if (steps <= 0) return [];
  const base = durationMs / steps;
  return Array.from({ length: steps }, () =>
    Math.max(4, Math.round(base * gaussianRandom(1, 0.15))),
  );
}

/**
 * Pre-click hover pause (ms) — humans hover briefly before clicking.
 */
export function preClickHover(): number {
  return Math.round(40 + Math.random() * 120);
}

/**
 * Between-action thinking pause (ms).
 * `complexity` drives the expected pause length.
 */
export function thinkingPause(
  complexity: "instant" | "simple" | "reading" | "complex" = "simple",
): number {
  switch (complexity) {
    case "instant":
      return 0;
    case "simple":
      return Math.round(gaussianRandom(120, 40));
    case "reading":
      return Math.round(gaussianRandom(600, 200));
    case "complex":
      return Math.round(gaussianRandom(1200, 400));
  }
}
