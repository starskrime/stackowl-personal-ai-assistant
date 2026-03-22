/**
 * StackOwl — Learning Module Exports
 */

// Core orchestration
export { LearningOrchestrator } from './orchestrator.js';
export type { LearningCycle, LearningStats } from './orchestrator.js';

// Signal processing
export { TopicFusionEngine } from './topic-fusion.js';
export { ConversationExtractor } from './extractor.js';
export type { ConversationInsights } from './extractor.js';
export type { FusedTopic, FusionResult, SynthesisStrategy, SourceSignal } from './topic-fusion.js';

// Knowledge synthesis
export { KnowledgeSynthesizer } from './synthesizer.js';
export type { SynthesisContext, SynthesisResult, SynthesisReport } from './synthesizer.js';

// Existing (kept for backwards compatibility)
export { KnowledgeGraphManager } from './knowledge-graph.js';
export type { DomainNode, KnowledgeGraph } from './knowledge-graph.js';
export { SelfHealer } from './self-healer.js';
export { MicroLearner } from './micro-learner.js';
export { ProactiveAnticipator } from './anticipator.js';
export type { Anticipation } from './anticipator.js';

// Memory exports (from memory/reflexion.ts)
export { MemoryReflexionEngine } from '../memory/reflexion.js';
export type {
  MemoryEntry,
  MemoryStore,
  MemoryCategory,
  MemorySource,
  ReflexionResult,
  ConsolidationResult,
} from '../memory/reflexion.js';
