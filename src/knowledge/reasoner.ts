import { Logger } from '../logger.js';
import type { ModelProvider, ChatMessage } from '../providers/base.js';
import type { KnowledgeGraph } from './graph.js';
import type { KnowledgeEdge, ReasoningChain, ReasoningStep, EdgeType } from './types.js';

const log = new Logger('REASONER');

interface Session {
  id: string;
  messages: { role: string; content: string }[];
  metadata: { owlName: string; startedAt: number; lastUpdatedAt: number; title?: string };
}

interface ExtractedFact {
  title: string;
  content: string;
  domain: string;
  confidence: number;
  relations: { to_title: string; type: EdgeType }[];
}

interface LLMReasoningResult {
  steps: { nodeId: string; contribution: string }[];
  conclusion: string;
  confidence: number;
}

export class KnowledgeReasoner {
  constructor(
    private graph: KnowledgeGraph,
    private provider: ModelProvider
  ) {}

  async reason(query: string, maxDepth = 3): Promise<ReasoningChain> {
    const matchingNodes = this.graph.search(query, 5);

    if (matchingNodes.length === 0) {
      return {
        query,
        steps: [],
        conclusion: 'No relevant knowledge found in the graph.',
        confidence: 0,
        timestamp: new Date().toISOString(),
      };
    }

    const visited = new Set<string>();
    const subgraphNodeIds = new Set<string>();

    for (const node of matchingNodes) {
      this.bfsCollect(node.id, maxDepth, visited, subgraphNodeIds);
    }

    const subgraphNodes = Array.from(subgraphNodeIds)
      .map(id => this.graph.getNode(id))
      .filter(n => n !== undefined);

    const subgraphEdges: KnowledgeEdge[] = [];
    const edgeSeen = new Set<string>();
    for (const nodeId of subgraphNodeIds) {
      for (const edge of this.graph.getEdges(nodeId)) {
        if (subgraphNodeIds.has(edge.from) && subgraphNodeIds.has(edge.to) && !edgeSeen.has(edge.id)) {
          subgraphEdges.push(edge);
          edgeSeen.add(edge.id);
        }
      }
    }

    const nodesText = subgraphNodes
      .map(n => `[${n.id}] ${n.title} (confidence: ${n.confidence}): ${n.content}`)
      .join('\n');

    const edgesText = subgraphEdges
      .map(e => `[${e.from}] --${e.type}--> [${e.to}]`)
      .join('\n');

    const prompt = `Given this knowledge graph subgraph, construct a reasoning chain to answer the query.

Query: ${query}

Available knowledge:
${nodesText}

Relationships:
${edgesText || '(none)'}

Build a step-by-step reasoning chain using these nodes. Output JSON only, no other text:
{
  "steps": [{"nodeId": "...", "contribution": "..."}],
  "conclusion": "...",
  "confidence": 0.0
}`;

    try {
      const messages: ChatMessage[] = [
        { role: 'system', content: 'You are a reasoning engine. Output valid JSON only.' },
        { role: 'user', content: prompt },
      ];

      const response = await this.provider.chat(messages, undefined, { temperature: 0.3 });
      const parsed = this.parseJSON<LLMReasoningResult>(response.content);

      const steps: ReasoningStep[] = parsed.steps
        .map(step => {
          const node = this.graph.getNode(step.nodeId);
          if (!node) return null;
          const edges = this.graph.getEdges(step.nodeId);
          const relevantEdge = edges.find(e =>
            parsed.steps.some(s => s.nodeId === e.from || s.nodeId === e.to)
          );
          return {
            nodeId: step.nodeId,
            nodeTitle: node.title,
            edgeType: relevantEdge?.type,
            contribution: step.contribution,
          };
        })
        .filter((s): s is ReasoningStep => s !== null);

      return {
        query,
        steps,
        conclusion: parsed.conclusion,
        confidence: Math.max(0, Math.min(1, parsed.confidence)),
        timestamp: new Date().toISOString(),
      };
    } catch (err) {
      log.error(`Reasoning failed: ${err}`);
      return {
        query,
        steps: matchingNodes.map(n => ({
          nodeId: n.id,
          nodeTitle: n.title,
          contribution: n.content,
        })),
        conclusion: 'Reasoning chain could not be constructed via LLM; returning raw matches.',
        confidence: 0.3,
        timestamp: new Date().toISOString(),
      };
    }
  }

  async extractFromConversation(messages: ChatMessage[], domain?: string): Promise<string[]> {
    const recentMessages = messages.slice(-10);
    const conversationText = recentMessages
      .map(m => `[${m.role}]: ${m.content}`)
      .join('\n');

    const prompt = `Extract discrete knowledge facts from this conversation. For each fact, specify:
- title (short)
- content (1-2 sentences)
- domain (broad category)
- confidence (0-1)
- relationships to other facts (type: supports/contradicts/extends/related)

${domain ? `Default domain: ${domain}` : ''}

Conversation:
${conversationText}

Output JSON array only, no other text:
[{"title": "...", "content": "...", "domain": "...", "confidence": 0.8, "relations": [{"to_title": "...", "type": "supports"}]}]`;

    try {
      const chatMessages: ChatMessage[] = [
        { role: 'system', content: 'You are a knowledge extraction engine. Output valid JSON only.' },
        { role: 'user', content: prompt },
      ];

      const response = await this.provider.chat(chatMessages, undefined, { temperature: 0.2 });
      const facts = this.parseJSON<ExtractedFact[]>(response.content);

      if (!Array.isArray(facts)) {
        log.warn('LLM did not return an array of facts');
        return [];
      }

      const newNodeIds: string[] = [];
      const titleToId = new Map<string, string>();

      for (const existingNode of this.graph.getAllNodes()) {
        titleToId.set(existingNode.title.toLowerCase(), existingNode.id);
      }

      for (const fact of facts) {
        const nodeId = this.graph.addNode({
          title: fact.title,
          content: fact.content,
          domain: fact.domain || domain || 'general',
          confidence: Math.max(0, Math.min(1, fact.confidence)),
          source: 'conversation',
        });
        newNodeIds.push(nodeId);
        titleToId.set(fact.title.toLowerCase(), nodeId);
      }

      for (let i = 0; i < facts.length; i++) {
        const fact = facts[i];
        const fromId = newNodeIds[i];
        if (!fact.relations) continue;

        for (const rel of fact.relations) {
          const toId = titleToId.get(rel.to_title.toLowerCase());
          if (toId && toId !== fromId) {
            try {
              this.graph.addEdge(fromId, toId, rel.type, 0.5);
            } catch {
              log.debug(`Skipped edge from ${fromId} to ${toId}: node missing`);
            }
          }
        }
      }

      log.info(`Extracted ${newNodeIds.length} knowledge nodes from conversation`);
      return newNodeIds;
    } catch (err) {
      log.error(`Knowledge extraction failed: ${err}`);
      return [];
    }
  }

  findPath(fromId: string, toId: string): KnowledgeEdge[] {
    if (fromId === toId) return [];

    const visited = new Set<string>();
    const queue: { nodeId: string; path: KnowledgeEdge[] }[] = [{ nodeId: fromId, path: [] }];
    visited.add(fromId);

    while (queue.length > 0) {
      const current = queue.shift()!;
      const edges = this.graph.getEdges(current.nodeId);

      for (const edge of edges) {
        const neighborId = edge.from === current.nodeId ? edge.to : edge.from;
        if (visited.has(neighborId)) continue;
        visited.add(neighborId);

        const newPath = [...current.path, edge];
        if (neighborId === toId) return newPath;

        queue.push({ nodeId: neighborId, path: newPath });
      }
    }

    return [];
  }

  formatChainForContext(chain: ReasoningChain): string {
    const stepsXml = chain.steps
      .map(step => {
        const rel = step.edgeType ? ` relation="${step.edgeType}"` : '';
        return `  <step node="${step.nodeTitle}"${rel}>${step.contribution}</step>`;
      })
      .join('\n');

    return `<reasoning_chain query="${chain.query}">
${stepsXml}
  <conclusion confidence="${chain.confidence.toFixed(2)}">${chain.conclusion}</conclusion>
</reasoning_chain>`;
  }

  private bfsCollect(startId: string, maxDepth: number, visited: Set<string>, collected: Set<string>): void {
    const queue: { nodeId: string; depth: number }[] = [{ nodeId: startId, depth: 0 }];
    visited.add(startId);
    collected.add(startId);

    while (queue.length > 0) {
      const current = queue.shift()!;
      if (current.depth >= maxDepth) continue;

      const edges = this.graph.getEdges(current.nodeId);
      for (const edge of edges) {
        const neighborId = edge.from === current.nodeId ? edge.to : edge.from;
        if (visited.has(neighborId)) continue;
        visited.add(neighborId);
        collected.add(neighborId);
        queue.push({ nodeId: neighborId, depth: current.depth + 1 });
      }
    }
  }

  private parseJSON<T>(text: string): T {
    const cleaned = text.replace(/```json\s*/g, '').replace(/```\s*/g, '').trim();
    return JSON.parse(cleaned);
  }
}
