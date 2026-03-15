export type EdgeType = 'supports' | 'contradicts' | 'extends' | 'supersedes' | 'related' | 'requires' | 'caused_by';

export interface KnowledgeNode {
  id: string;
  title: string;
  content: string;
  source: string;
  domain: string;
  confidence: number;
  createdAt: string;
  updatedAt: string;
  accessCount: number;
}

export interface KnowledgeEdge {
  id: string;
  from: string;
  to: string;
  type: EdgeType;
  weight: number;
  evidence?: string;
  createdAt: string;
}

export interface ReasoningStep {
  nodeId: string;
  nodeTitle: string;
  edgeType?: EdgeType;
  contribution: string;
}

export interface ReasoningChain {
  query: string;
  steps: ReasoningStep[];
  conclusion: string;
  confidence: number;
  timestamp: string;
}

export interface GraphStats {
  totalNodes: number;
  totalEdges: number;
  domains: string[];
  avgConfidence: number;
  topNodes: { id: string; title: string; accessCount: number }[];
}
