import type { TrendAnalysis } from './types.js';

export interface DnaTraits {
  challengeLevel: 'low' | 'medium' | 'high' | 'relentless';
  verbosity: 'verbose' | 'balanced' | 'concise';
  humor: number;
  formality: number;
  proactivity: number;
  riskTolerance: 'cautious' | 'moderate' | 'aggressive';
  teachingStyle: 'examples' | 'direct' | 'adaptive';
  delegationPreference: 'autonomous' | 'collaborative' | 'confirmatory';
}

export interface MutationSuggestion {
  trait: keyof DnaTraits;
  direction: 'increase' | 'decrease' | 'change';
  currentValue: DnaTraits[keyof DnaTraits];
  suggestedValue: DnaTraits[keyof DnaTraits];
  reason: string;
}

export interface MutationResult {
  applied: MutationSuggestion[];
  rejected: MutationSuggestion[];
  beforeState: Partial<DnaTraits>;
  afterState: Partial<DnaTraits>;
}

export interface MutationEngineConfig {
  minTraitValue: number;
  maxTraitValue: number;
  mutationStepSize: number;
  maxMutationsPerTrait: number;
}

const DEFAULT_CONFIG: MutationEngineConfig = {
  minTraitValue: 0.0,
  maxTraitValue: 1.0,
  mutationStepSize: 0.1,
  maxMutationsPerTrait: 3,
};

export class DNAMutationEngine {
  private config: MutationEngineConfig;
  private currentTraits: DnaTraits;

  constructor(
    traits: DnaTraits,
    config: Partial<MutationEngineConfig> = {}
  ) {
    this.config = { ...DEFAULT_CONFIG, ...config };
    this.currentTraits = { ...traits };
  }

  getTraits(): Readonly<DnaTraits> {
    return { ...this.currentTraits };
  }

  generateSuggestions(analysis: TrendAnalysis): MutationSuggestion[] {
    const suggestions: MutationSuggestion[] = [];
    const seenTraits = new Set<string>();

    for (const recommendation of analysis.recommendations) {
      const suggestion = this.parseRecommendation(recommendation);
      if (suggestion && !seenTraits.has(suggestion.trait)) {
        suggestions.push(suggestion);
        seenTraits.add(suggestion.trait);
      }
    }

    for (const pattern of analysis.patterns) {
      const suggestion = this.inferSuggestionFromPattern(pattern);
      if (suggestion && !seenTraits.has(suggestion.trait)) {
        suggestions.push(suggestion);
        seenTraits.add(suggestion.trait);
      }
    }

    return suggestions;
  }

  private parseRecommendation(recommendation: string): MutationSuggestion | null {
    const lowerRec = recommendation.toLowerCase();

    if (lowerRec.includes('humor')) {
      return this.createNumericSuggestion('humor', lowerRec);
    }
    if (lowerRec.includes('formality')) {
      return this.createNumericSuggestion('formality', lowerRec);
    }
    if (lowerRec.includes('proactivity')) {
      return this.createNumericSuggestion('proactivity', lowerRec);
    }
    if (lowerRec.includes('delegationpreference')) {
      return this.createEnumSuggestion('delegationPreference', lowerRec);
    }
    if (lowerRec.includes('teachingstyle')) {
      return this.createEnumSuggestion('teachingStyle', lowerRec);
    }
    if (lowerRec.includes('risktolerance')) {
      return this.createEnumSuggestion('riskTolerance', lowerRec);
    }

    return null;
  }

  private createNumericSuggestion(
    trait: 'humor' | 'formality' | 'proactivity',
    context: string
  ): MutationSuggestion {
    const currentValue = this.currentTraits[trait];
    let direction: 'increase' | 'decrease' | 'change' = 'change';
    let suggestedValue = currentValue;

    if (context.includes('increase') || context.includes('too low') || context.includes('not enough')) {
      direction = 'increase';
      suggestedValue = Math.min(
        this.config.maxTraitValue,
        currentValue + this.config.mutationStepSize
      );
    } else if (context.includes('decrease') || context.includes('too high') || context.includes('excessive') || context.includes('too much')) {
      direction = 'decrease';
      suggestedValue = Math.max(
        this.config.minTraitValue,
        currentValue - this.config.mutationStepSize
      );
    }

    return {
      trait,
      direction,
      currentValue,
      suggestedValue,
      reason: `Based on behavioral analysis: ${context}`,
    };
  }

  private createEnumSuggestion(
    trait: 'riskTolerance' | 'teachingStyle' | 'delegationPreference',
    context: string
  ): MutationSuggestion {
    const currentValue = this.currentTraits[trait];
    const direction: 'change' = 'change';
    let suggestedValue = currentValue;

    if (trait === 'riskTolerance') {
      if (context.includes('too cautious') || context.includes('too passive') || context.includes('needs more assertiveness')) {
        suggestedValue = currentValue === 'cautious' ? 'moderate' : 'aggressive';
      } else if (context.includes('too aggressive') || context.includes('too risky') || context.includes('more cautious')) {
        suggestedValue = currentValue === 'aggressive' ? 'moderate' : 'cautious';
      }
    } else if (trait === 'teachingStyle') {
      if (context.includes('too simple') || context.includes('needs more depth')) {
        suggestedValue = currentValue === 'examples' ? 'direct' : 'adaptive';
      } else if (context.includes('too complex') || context.includes('needs more examples')) {
        suggestedValue = currentValue === 'direct' ? 'examples' : 'adaptive';
      }
    } else if (trait === 'delegationPreference') {
      if (context.includes('too autonomous') || context.includes('should ask more')) {
        suggestedValue = currentValue === 'autonomous' ? 'collaborative' : 'confirmatory';
      } else if (context.includes('asks too much') || context.includes('should do more')) {
        suggestedValue = currentValue === 'confirmatory' ? 'collaborative' : 'autonomous';
      }
    }

    return {
      trait,
      direction,
      currentValue,
      suggestedValue,
      reason: `Based on behavioral analysis: ${context}`,
    };
  }

  private inferSuggestionFromPattern(pattern: string): MutationSuggestion | null {
    const lowerPattern = pattern.toLowerCase();

    if (lowerPattern.includes('high error rate') || lowerPattern.includes('frequent failures')) {
      if (this.currentTraits.riskTolerance !== 'cautious') {
        return {
          trait: 'riskTolerance',
          direction: 'change',
          currentValue: this.currentTraits.riskTolerance,
          suggestedValue: 'cautious',
          reason: `Error pattern detected: ${pattern}`,
        };
      }
    }

    if (lowerPattern.includes('low engagement') || lowerPattern.includes('low interaction')) {
      if (this.currentTraits.proactivity < 0.5) {
        return {
          trait: 'proactivity',
          direction: 'increase',
          currentValue: this.currentTraits.proactivity,
          suggestedValue: Math.min(1.0, this.currentTraits.proactivity + this.config.mutationStepSize),
          reason: `Engagement pattern detected: ${pattern}`,
        };
      }
    }

    return null;
  }

  applyMutation(suggestion: MutationSuggestion): boolean {
    if (!this.validateMutation(suggestion)) {
      this.logMutationRejection(suggestion);
      return false;
    }

    const beforeValue = this.currentTraits[suggestion.trait];
    this.currentTraits[suggestion.trait] = suggestion.suggestedValue as never;
    this.logMutationApplied(suggestion, beforeValue);
    return true;
  }

  applyMutations(suggestions: MutationSuggestion[]): MutationResult {
    const beforeState = { ...this.currentTraits };
    const applied: MutationSuggestion[] = [];
    const rejected: MutationSuggestion[] = [];

    for (const suggestion of suggestions) {
      if (this.applyMutation(suggestion)) {
        applied.push(suggestion);
      } else {
        rejected.push(suggestion);
      }
    }

    return {
      applied,
      rejected,
      beforeState,
      afterState: { ...this.currentTraits },
    };
  }

  validateMutation(suggestion: MutationSuggestion): boolean {
    const currentValue = this.currentTraits[suggestion.trait];

    if (currentValue !== suggestion.currentValue) {
      return false;
    }

    if (typeof suggestion.suggestedValue === 'number') {
      const value = suggestion.suggestedValue as number;
      if (value < this.config.minTraitValue || value > this.config.maxTraitValue) {
        return false;
      }
    }

    return true;
  }

  private logMutationApplied(suggestion: MutationSuggestion, beforeValue: DnaTraits[keyof DnaTraits]): void {
    const timestamp = new Date().toISOString();
    console.log(
      `${timestamp} INFO [DNAMutationEngine] behavioral.evolution.dna_mutated trait=${suggestion.trait} before=${beforeValue} after=${suggestion.suggestedValue} reason="${suggestion.reason}"`
    );
  }

  private logMutationRejection(suggestion: MutationSuggestion): void {
    const timestamp = new Date().toISOString();
    console.log(
      `${timestamp} WARN [DNAMutationEngine] behavioral.evolution.dna_mutation_rejected trait=${suggestion.trait} current=${suggestion.currentValue} rejected=${suggestion.suggestedValue}`
    );
  }

  resetTraits(traits: DnaTraits): void {
    this.currentTraits = { ...traits };
  }
}
