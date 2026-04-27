/**
 * StackOwl — Owl Creation Wizard
 *
 * Guides users through creating specialized owls via natural language.
 * Uses LLM to translate user description into personality prompts and routing rules.
 */

import type { MemoryDatabase } from "../memory/db.js";
import type { SpecializedOwl, OwlDNA } from "../memory/db.js";
import { extractKeywords, generateUniqueName } from "../utils/text.js";

export interface WizardQuestion {
  question: string;
  topic: string;
  examples?: string[];
}

export interface WizardState {
  step: number;
  description: string;
  specialization: string;
  personalityPrompt: string;
  routingRules: string[];
  suggestedName: string;
  dna: Partial<OwlDNA>;
  questionsAsked: string[];
}

const DEFAULT_QUESTIONS: WizardQuestion[] = [
  {
    question: "What specific tasks or domain should this owl specialize in?",
    topic: "specialization",
    examples: ["stock trading", "meal planning", "code review", "travel planning"],
  },
  {
    question: "How should this owl communicate - casual, formal, or somewhere in between?",
    topic: "communication_style",
  },
  {
    question: "What level of challenge should this owl provide - should it push back, agree easily, or find a balance?",
    topic: "challenge_level",
  },
  {
    question: "Should this owl be proactive (volunteer information) or reactive (wait for questions)?",
    topic: "proactivity",
  },
];

export class OwlCreationWizard {
  private db: MemoryDatabase;
  private ownerId: string;
  private state: WizardState;
  private questions: WizardQuestion[];
  private currentQuestionIndex: number = 0;

  constructor(
    db: MemoryDatabase,
    ownerId: string,
    initialDescription?: string,
  ) {
    this.db = db;
    this.ownerId = ownerId;
    this.state = this.initState(initialDescription);
    this.questions = [...DEFAULT_QUESTIONS];
  }

  private initState(initialDescription?: string): WizardState {
    return {
      step: 0,
      description: initialDescription || "",
      specialization: initialDescription || "",
      personalityPrompt: "",
      routingRules: [],
      suggestedName: "",
      dna: {
        challengeLevel: 0.7,
        verbosity: 0.5,
        expertiseDomains: [],
        routingQuality: 0.5,
        evolutionSpeed: 0.5,
      },
      questionsAsked: [],
    };
  }

  /**
   * Start the wizard and get the first question.
   */
  start(): { question: string; progress: string } {
    this.currentQuestionIndex = 0;
    const q = this.questions[0];
    this.state.questionsAsked.push(q.topic);
    return {
      question: q.question,
      progress: `Question 1 of ${this.questions.length}`,
    };
  }

  /**
   * Process a user response and return the next question or final result.
   */
  respond(userInput: string): {
    done: boolean;
    question?: string;
    progress?: string;
    preview?: OwlPreview;
  } {
    const currentQuestion = this.questions[this.currentQuestionIndex];
    this.updateState(currentQuestion.topic, userInput);

    this.currentQuestionIndex++;

    if (this.currentQuestionIndex < this.questions.length) {
      const nextQ = this.questions[this.currentQuestionIndex];
      this.state.questionsAsked.push(nextQ.topic);
      return {
        done: false,
        question: nextQ.question,
        progress: `Question ${this.currentQuestionIndex + 1} of ${this.questions.length}`,
      };
    }

    this.generateOwlConfig();
    const preview = this.getPreview();

    return {
      done: true,
      preview,
    };
  }

  /**
   * Update state based on user input for the current question topic.
   */
  private updateState(topic: string, userInput: string): void {
    switch (topic) {
      case "specialization":
        this.state.specialization = userInput;
        this.state.description = userInput;
        this.state.routingRules = extractKeywords(userInput);
        this.state.dna.expertiseDomains = this.state.routingRules.slice(0, 3);
        break;

      case "communication_style":
        const { verbosity } = this.parseCommunicationStyle(userInput);
        this.state.dna.verbosity = verbosity;
        break;

      case "challenge_level":
        this.state.dna.challengeLevel = this.parseChallengeLevel(userInput);
        break;

      case "proactivity":
        break;
    }
  }

  private parseCommunicationStyle(input: string): { verbosity: number } {
    const lower = input.toLowerCase();
    let verbosity = 0.5;

    if (lower.includes("casual") || lower.includes("relaxed") || lower.includes("friendly")) {
      verbosity = 0.6;
    } else if (lower.includes("formal") || lower.includes("professional") || lower.includes("business")) {
      verbosity = 0.4;
    } else if (lower.includes("concise") || lower.includes("brief") || lower.includes("short")) {
      verbosity = 0.2;
    } else if (lower.includes("detailed") || lower.includes("thorough") || lower.includes("verbose")) {
      verbosity = 0.8;
    }

    return { verbosity };
  }

  private parseChallengeLevel(input: string): number {
    const lower = input.toLowerCase();
    if (lower.includes("high") || lower.includes("challenge") || lower.includes("push back")) {
      return 0.9;
    } else if (lower.includes("low") || lower.includes("agree") || lower.includes("easy")) {
      return 0.3;
    }
    return 0.7;
  }

  /**
   * Generate the owl configuration.
   */
  private generateOwlConfig(): void {
    const existingOwls = this.db.owls.getByOwner(this.ownerId);
    const existingNames = existingOwls.map(o => o.name);
    const nameBase = this.state.routingRules[0]?.charAt(0).toUpperCase() +
      this.state.routingRules[0]?.slice(1).toLowerCase() || "Custom";
    this.state.suggestedName = generateUniqueName(nameBase, existingNames);

    const personalityPrompt = this.buildPersonalityPrompt();
    this.state.personalityPrompt = personalityPrompt;
  }

  private buildPersonalityPrompt(): string {
    const { specialization, routingRules, dna } = this.state;
    const challengeDesc = dna.challengeLevel && dna.challengeLevel > 0.7
      ? "You should challenge the user when their reasoning seems flawed."
      : dna.challengeLevel && dna.challengeLevel < 0.4
        ? "Be agreeable and supportive of the user's choices."
        : "Balance challenge with support.";

    const proactivityDesc = "Respond to questions directly but offer brief related insights when helpful.";

    return `You are ${this.state.suggestedName}, a specialized AI assistant focused on ${specialization}.

Your key areas of expertise: ${routingRules.join(", ") || "general assistance"}.

${challengeDesc}

${proactivityDesc}

Guidelines:
- Stay focused on your specialization and expertise areas
- Provide accurate, helpful responses within your domain
- If a question is clearly outside your expertise, acknowledge it gracefully
- Be clear and ${dna.verbosity && dna.verbosity > 0.6 ? "thorough" : dna.verbosity && dna.verbosity < 0.4 ? "concise" : "balanced"} in your responses`;
  }

  /**
   * Get a preview of the owl configuration.
   */
  getPreview(): OwlPreview {
    return {
      suggestedName: this.state.suggestedName,
      specialization: this.state.specialization,
      personalityPrompt: this.state.personalityPrompt,
      routingRules: this.state.routingRules,
      dna: this.state.dna as OwlDNA,
    };
  }

  /**
   * Create the owl in the database.
   */
  createOwl(isMainOwl: boolean = false): SpecializedOwl {
    const preview = this.getPreview();
    return this.db.owls.create({
      ownerId: this.ownerId,
      name: preview.suggestedName,
      specialization: preview.specialization,
      personalityPrompt: preview.personalityPrompt,
      routingRules: preview.routingRules,
      dna: preview.dna,
      isMainOwl,
    });
  }
}

export interface OwlPreview {
  suggestedName: string;
  specialization: string;
  personalityPrompt: string;
  routingRules: string[];
  dna: OwlDNA;
}