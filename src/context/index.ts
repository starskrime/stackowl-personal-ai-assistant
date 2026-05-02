export type { ContextLayer, ContextRequest, ContextDependencies, TriageSignals,
  LayerResults, ContextBuildTrace, ContextBuildTraceEntry } from "./layer.js";
export { computeTriage } from "./triage.js";
export { resolveUserId, hash } from "./utils.js";
export { BudgetController, estimateTokens } from "./budget-controller.js";
export { DAGPlanner, CircularDependencyError } from "./dag-planner.js";
export { ContextCache } from "./cache.js";
export { LayerCircuitBreaker, LayerHealthMonitor, ContextQualityScore } from "./circuit-breaker.js";
export { ContextPipeline } from "./pipeline.js";
export { UserPersonaSynthesizer } from "./user-persona-synthesizer.js";
export type { UserPersona } from "./user-persona-synthesizer.js";
export { UnifiedMemoryRetriever } from "./unified-memory-retriever.js";

import { SynthesisIdentityLayer } from "./layers/identity.js";
import { InnerMonologueLayer } from "./layers/inner-monologue.js";
import { WorkingMemoryDigestLayer, ContinuityPriorResponseLayer, CompressionSummaryLayer } from "./layers/working-memory.js";
import { CrossSessionFactsLayer, OpenTasksLayer, RelationshipContextLayer } from "./layers/user-memory.js";
import { UserPersonaLayer } from "./layers/user-persona.js";
import { BehavioralPatchLayer, ActiveIntentsLayer, OwlLearningsLayer } from "./layers/behavioral.js";
import { TemporalAwarenessLayer, ChannelFormatHintLayer, ModeDirectiveLayer, SocraticModeLayer } from "./layers/infrastructure.js";
import { UnifiedMemoryRetrievalLayer } from "./layers/memory-retrieval.js";
import { KnowledgeGraphLayer, RelevantPelletsLayer } from "./layers/knowledge.js";
import { UserBehaviorProfileLayer, InferredPreferencesLayer, PredictedNeedsLayer } from "./layers/profile.js";
import { CollabContextLayer, AmbientContextLayer } from "./layers/ambient.js";
import { DepthDirectiveLayer, OpinionInjectionLayer, UserMentalModelLayer, EchoChamberGuardLayer, GroundStateLayer } from "./layers/calibration.js";
import { DAGPlanner } from "./dag-planner.js";
import { ContextCache } from "./cache.js";
import { LayerHealthMonitor } from "./circuit-breaker.js";
import { ContextPipeline } from "./pipeline.js";
import type { UserPersonaSynthesizer } from "./user-persona-synthesizer.js";
import type { UnifiedMemoryRetriever } from "./unified-memory-retriever.js";
import type { ContextLayer } from "./layer.js";
import { CritiqueRetriever } from "../intelligence/critique-retriever.js";
import type { MemoryDatabase } from "../memory/db.js";

export interface ContextPipelineDeps {
  userPersonaSynthesizer: UserPersonaSynthesizer;
  unifiedMemoryRetriever: UnifiedMemoryRetriever;
  contextCache?: ContextCache;  // if provided, used by the pipeline; otherwise creates its own
  /** Optional MemoryDatabase — enables the CritiqueRetriever layer when provided. */
  db?: MemoryDatabase;
}

export function createContextPipeline(deps: ContextPipelineDeps): ContextPipeline {
  const layers: ContextLayer[] = [
    new SynthesisIdentityLayer(),
    new InnerMonologueLayer(),
    new WorkingMemoryDigestLayer(),
    new ContinuityPriorResponseLayer(),
    new CompressionSummaryLayer(),
    new CrossSessionFactsLayer(),
    new OpenTasksLayer(),
    new RelationshipContextLayer(),
    new UserPersonaLayer(deps.userPersonaSynthesizer),
    new TemporalAwarenessLayer(),
    new ChannelFormatHintLayer(),
    new ModeDirectiveLayer(),
    new SocraticModeLayer(),
    new BehavioralPatchLayer(),
    new ActiveIntentsLayer(),
    new OwlLearningsLayer(),
    new UnifiedMemoryRetrievalLayer(deps.unifiedMemoryRetriever),
    new KnowledgeGraphLayer(),
    new RelevantPelletsLayer(),
    new UserBehaviorProfileLayer(),
    new InferredPreferencesLayer(),
    new PredictedNeedsLayer(),
    new CollabContextLayer(),
    new AmbientContextLayer(),
    new DepthDirectiveLayer(),
    new OpinionInjectionLayer(),
    new UserMentalModelLayer(),
    new EchoChamberGuardLayer(),
    new GroundStateLayer(),
  ];

  // CritiqueRetriever: inject past failure lessons before the LLM starts a task
  if (deps.db) {
    const critiqueRetriever = new CritiqueRetriever(deps.db);
    layers.push(critiqueRetriever.asContextLayer());
  }

  return new ContextPipeline(
    layers,
    deps.contextCache ?? new ContextCache(),
    new LayerHealthMonitor(),
    new DAGPlanner(),
  );
}
