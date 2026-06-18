/**
 * STUB — unclarity-surfacer.ts
 *
 * The original regex-based UnclaritySurfacer has been deleted (Task 3).
 * This stub keeps the module graph intact while gateway/core.ts is awaiting
 * its Task 11 rewrite. Remove this file and all references in gateway/core.ts
 * once Task 11 is complete.
 */

export class UnclaritySurfacer {
  detectUnclarity(_text: string, _priorMessages?: unknown[]): null {
    return null;
  }
  shouldSurfaceProactively(_text: string, _priorMessages?: unknown[]): false {
    return false;
  }
  surfaceUnclarity(_unclarity: unknown): string {
    return "";
  }
}

export const unclaritySurfacer = new UnclaritySurfacer();
