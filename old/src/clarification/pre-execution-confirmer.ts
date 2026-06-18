/**
 * STUB — pre-execution-confirmer.ts
 *
 * The original regex-based PreExecutionConfirmer has been deleted (Task 3).
 * This stub keeps the module graph intact while gateway/core.ts is awaiting
 * its Task 11 rewrite. Remove this file and all references in gateway/core.ts
 * once Task 11 is complete.
 */

export class PreExecutionConfirmer {
  assessRequest(_text: string): { needsConfirmation: false; confidence: number } {
    return { needsConfirmation: false, confidence: 1 };
  }
  getConfirmationQuestion(_result: unknown): string {
    return "";
  }
}

export const preExecutionConfirmer = new PreExecutionConfirmer();
