/**
 * StackOwl — Topic Fusion Engine
 *
 * Merges 4 signal types (topics, domains, gaps, researchQuestions) into
 * a single prioritized list with urgency scores and synthesis strategy routing.
 */

import { log } from '../logger.js';
import type { ConversationInsights } from './extractor.js';
import type { KnowledgeGraph } from './knowledge-graph.js';

// ─── Types ───────────────────────────────────────────────────────

export type SynthesisStrategy =
  | 'deep_research'   // User explicitly needs this, gap = high urgency
  | 'quick_lookup'    // Topic appeared once, just-in-time learning
  | 'watch_and_learn' // Observation mode, passive
  | 'repo_digest'     // Likely a codebase question
  | 'document_digest' // Likely docs or README question
  | 'web_research'    // Needs current web info
  | 'q_and_a'
  | 'repo_analysis';       // Self-generated Q&A (current behavior)

export type SourceSignal = 'topic' | 'domain' | 'gap' | 'question';

export interface FusedTopic {
  id: string;
  normalizedName: string;
  displayName: string;
  urgency: number;              // 0-100, calculated
  sourceSignals: SourceSignal[];
  originalSignals: string[];
  lastSeen: string;
  failureCount: number;
  relatedDomains: string[];
  synthesisStrategy: SynthesisStrategy;
  priorityOverride?: 'critical' | 'high' | 'low';
  sourceInsights: ConversationInsights[];
}

export interface FusionResult {
  fusedTopics: FusedTopic[];
  stats: {
    totalSignals: number;
    uniqueTopics: number;
    criticalCount: number;
    deduplicatedSignals: number;
  };
}

// ─── Aliases & Normalization ────────────────────────────────────

const ALIAS_MAP: Record<string, string> = {
  'openai': 'openai-api',
  'open ai': 'openai-api',
  'open-ai': 'openai-api',
  'claude': 'anthropic-claude',
  'claude api': 'anthropic-claude',
  'llm': 'large-language-models',
  'llms': 'large-language-models',
  'ai model': 'large-language-models',
  'ai models': 'large-language-models',
  'machine learning': 'machine-learning',
  'ml': 'machine-learning',
  'web scraping': 'web-scraping',
  'web_scraping': 'web-scraping',
  'webscraping': 'web-scraping',
  'web search': 'web-search',
  'api': 'api-development',
  'apis': 'api-development',
  'docker': 'docker-containers',
  'kubernetes': 'kubernetes-k8s',
  'k8s': 'kubernetes-k8s',
  'typescript': 'typescript-lang',
  'ts': 'typescript-lang',
  'javascript': 'javascript-js',
  'js': 'javascript-js',
  'python': 'python-lang',
  'rust': 'rust-lang',
  'go': 'golang',
  'golang': 'golang',
  'postgresql': 'postgresql-db',
  'postgres': 'postgresql-db',
  'pg': 'postgresql-db',
  'mongodb': 'mongodb-db',
  'mongo': 'mongodb-db',
  'redis': 'redis-cache',
};

export function normalizeTopic(raw: string): string {
  let normalized = raw
    .toLowerCase()
    .trim()
    .replace(/[._-]+/g, '-')
    .replace(/\s+/g, '-')
    .replace(/[^a-z0-9-]/g, '')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '');

  if (ALIAS_MAP[normalized]) {
    normalized = ALIAS_MAP[normalized];
  }

  return normalized || raw.toLowerCase().trim();
}

export function toDisplayName(normalized: string): string {
  return normalized
    .split('-')
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

// ─── Strategy Routing ───────────────────────────────────────────

const URL_PATTERNS = /\b(https?:\/\/|www\.)\S+/i;
const REPO_PATTERNS = /\b(github|gitlab|sourcehut)\S*|[\w-]+\/[\w.-]+|~\/\S+\.git\b/i;
const CODE_PATTERNS = /\b(function|class|const|let|var|import|export|def|fn|pub|struct|enum)\b/i;
const QUESTION_PATTERNS = /\b(how|what|why|when|where|which|who)\b/i;
const DOC_PATTERNS = /\b(readme|docs?|documentation|manual|guide|tutorial)\b/i;

function routeStrategy(signal: string, fused: Partial<FusedTopic>): SynthesisStrategy {
  const lower = signal.toLowerCase();

  if (fused.sourceSignals?.includes('gap')) {
    return fused.failureCount && fused.failureCount >= 2 ? 'deep_research' : 'quick_lookup';
  }

  if (fused.sourceSignals?.includes('question')) {
    return 'deep_research';
  }

  if (URL_PATTERNS.test(signal)) {
    return REPO_PATTERNS.test(signal) ? 'repo_digest' : 'document_digest';
  }

  if (REPO_PATTERNS.test(lower) || lower.includes('repo') || lower.includes('codebase')) {
    return 'repo_digest';
  }

  if (CODE_PATTERNS.test(signal)) {
    return 'repo_digest';
  }

  if (DOC_PATTERNS.test(lower)) {
    return 'document_digest';
  }

  if (QUESTION_PATTERNS.test(lower) && /price|cost|latest|current|recent|today|news|status/i.test(lower)) {
    return 'web_research';
  }

  return 'q_and_a';
}

// ─── Urgency Scoring ────────────────────────────────────────────

const SIGNAL_WEIGHTS: Record<SourceSignal, number> = {
  gap: 40,
  question: 25,
  domain: 10,
  topic: 5,
};

function calculateUrgency(fused: FusedTopic): number {
  let score = 0;

  for (const signal of fused.sourceSignals) {
    score += SIGNAL_WEIGHTS[signal];
  }

  score += Math.min(25, fused.failureCount * 8);

  const ageMs = Date.now() - new Date(fused.lastSeen).getTime();
  const ageHours = ageMs / (1000 * 60 * 60);
  if (ageHours < 1) score += 10;
  else if (ageHours < 6) score += 5;
  else if (ageHours < 24) score += 2;

  if (fused.priorityOverride === 'critical') score = Math.max(score, 80);
  else if (fused.priorityOverride === 'high') score = Math.max(score, 60);
  else if (fused.priorityOverride === 'low') score = Math.max(score, 10);

  return Math.min(100, score);
}

// ─── Domain Extraction ──────────────────────────────────────────

function extractRelatedDomains(signals: string[]): string[] {
  const domainKeywords: Record<string, string> = {
    'web': 'web-development',
    'api': 'api-development',
    'database': 'databases',
    'db': 'databases',
    'cloud': 'cloud-computing',
    'docker': 'docker-containers',
    'kubernetes': 'kubernetes-k8s',
    'ai': 'artificial-intelligence',
    'ml': 'machine-learning',
    'devops': 'devops',
    'security': 'security',
    'network': 'networking',
    'code': 'programming',
    'script': 'scripting',
    'mobile': 'mobile-development',
    'frontend': 'frontend-development',
    'backend': 'backend-development',
    'openai': 'openai-api',
    'anthropic': 'anthropic-claude',
    'claude': 'anthropic-claude',
    'ollama': 'ollama-local-ai',
    'rust': 'rust-lang',
    'python': 'python-lang',
    'typescript': 'typescript-lang',
    'javascript': 'javascript-js',
    'golang': 'golang',
    'apple': 'apple-platforms',
    'macos': 'apple-platforms',
    'ios': 'mobile-development',
    'linux': 'linux-systems',
    'git': 'version-control',
    'github': 'version-control',
    'testing': 'software-testing',
    'ci': 'ci-cd',
    'cd': 'ci-cd',
    'monitoring': 'monitoring-observability',
  };

  const domains = new Set<string>();
  const combined = signals.join(' ').toLowerCase();

  for (const [keyword, domain] of Object.entries(domainKeywords)) {
    if (combined.includes(keyword)) {
      domains.add(domain);
    }
  }

  return [...domains].slice(0, 5);
}

// ─── TopicFusionEngine ─────────────────────────────────────────

export class TopicFusionEngine {
  async fuse(
    insights: ConversationInsights[],
    graph: KnowledgeGraph
  ): Promise<FusionResult> {
    if (insights.length === 0) {
      return {
        fusedTopics: [],
        stats: { totalSignals: 0, uniqueTopics: 0, criticalCount: 0, deduplicatedSignals: 0 },
      };
    }

    log.evolution.info(`[TopicFusion] Fusing ${insights.length} insight batch(es)...`);

    type RawSignal = { text: string; signal: SourceSignal; insight: ConversationInsights };
    const rawSignals: RawSignal[] = [];

    for (const insight of insights) {
      for (const topic of insight.topics) {
        rawSignals.push({ text: topic, signal: 'topic', insight });
      }
      for (const domain of insight.domains) {
        rawSignals.push({ text: domain, signal: 'domain', insight });
      }
      for (const gap of insight.knowledgeGaps) {
        rawSignals.push({ text: gap, signal: 'gap', insight });
      }
      for (const question of insight.researchQuestions) {
        rawSignals.push({ text: question, signal: 'question', insight });
      }
    }

    log.evolution.debug(`[TopicFusion] ${rawSignals.length} raw signals collected`);

    const fusedMap = new Map<string, FusedTopic>();

    for (const { text, signal, insight } of rawSignals) {
      const normalized = normalizeTopic(text);
      if (!normalized) continue;

      const displayName = text.trim();

      if (fusedMap.has(normalized)) {
        const existing = fusedMap.get(normalized)!;
        if (!existing.sourceSignals.includes(signal)) {
          existing.sourceSignals.push(signal);
        }
        if (!existing.originalSignals.includes(text.trim())) {
          existing.originalSignals.push(text.trim());
        }
        if (!existing.sourceInsights.includes(insight)) {
          existing.sourceInsights.push(insight);
        }
        if (new Date(insight.timestamp ?? Date.now()) > new Date(existing.lastSeen)) {
          existing.lastSeen = insight.timestamp ?? new Date().toISOString();
        }
        if (signal === 'gap') {
          existing.failureCount++;
        }
      } else {
        const now = insight.timestamp ?? new Date().toISOString();
        const relatedDomains = extractRelatedDomains([text]);
        const graphNode = graph.domains[normalized];
        const alreadyKnown = graphNode && graphNode.depth > 0.7;

        fusedMap.set(normalized, {
          id: normalized,
          normalizedName: normalized,
          displayName,
          urgency: 0,
          sourceSignals: [signal],
          originalSignals: [text.trim()],
          lastSeen: now,
          failureCount: signal === 'gap' ? 1 : 0,
          relatedDomains,
          synthesisStrategy: routeStrategy(text, { sourceSignals: [signal] }),
          priorityOverride: alreadyKnown ? 'low' : undefined,
          sourceInsights: [insight],
        });
      }
    }

    for (const fused of fusedMap.values()) {
      fused.urgency = calculateUrgency(fused);

      if (fused.urgency >= 60) {
        fused.synthesisStrategy = 'deep_research';
      } else if (fused.urgency >= 30) {
        fused.synthesisStrategy = 'q_and_a';
      } else {
        fused.synthesisStrategy = 'quick_lookup';
      }
    }

    const fusedTopics = [...fusedMap.values()].sort((a, b) => {
      if (a.priorityOverride === 'critical' && b.priorityOverride !== 'critical') return -1;
      if (b.priorityOverride === 'critical' && a.priorityOverride !== 'critical') return 1;
      if (a.priorityOverride === 'high' && b.priorityOverride === 'low') return -1;
      if (b.priorityOverride === 'high' && a.priorityOverride === 'low') return 1;
      return b.urgency - a.urgency;
    });

    const criticalCount = fusedTopics.filter(t => t.urgency >= 60 || t.priorityOverride === 'critical').length;
    const totalSignals = rawSignals.length;
    const uniqueTopics = fusedTopics.length;
    const deduplicatedSignals = totalSignals - uniqueTopics;

    log.evolution.evolve(
      `[TopicFusion] ${totalSignals} signals -> ${uniqueTopics} unique topics ` +
      `(${deduplicatedSignals} deduplicated), ${criticalCount} critical`
    );

    return {
      fusedTopics,
      stats: { totalSignals, uniqueTopics, criticalCount, deduplicatedSignals },
    };
  }

  async fuseSingle(
    insight: ConversationInsights,
    graph: KnowledgeGraph
  ): Promise<FusionResult> {
    return this.fuse([insight], graph);
  }
}
