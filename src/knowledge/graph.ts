import { randomUUID } from "node:crypto";
import { readFileSync, writeFileSync, existsSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { Logger } from "../logger.js";
import type {
  KnowledgeNode,
  KnowledgeEdge,
  EdgeType,
  GraphStats,
} from "./types.js";

const log = new Logger("KNOWLEDGE");

interface GraphData {
  nodes: KnowledgeNode[];
  edges: KnowledgeEdge[];
}

export class KnowledgeGraph {
  private nodes = new Map<string, KnowledgeNode>();
  private edges = new Map<string, KnowledgeEdge>();
  private filePath: string;

  constructor(private workspacePath: string) {
    this.filePath = join(workspacePath, "knowledge-graph.json");
  }

  async load(): Promise<void> {
    try {
      if (!existsSync(this.filePath)) {
        log.debug("No existing knowledge graph found, starting fresh");
        return;
      }
      const raw = readFileSync(this.filePath, "utf-8");
      const data: GraphData = JSON.parse(raw);
      this.nodes.clear();
      this.edges.clear();
      for (const node of data.nodes) {
        this.nodes.set(node.id, node);
      }
      for (const edge of data.edges) {
        this.edges.set(edge.id, edge);
      }
      log.info(
        `Loaded knowledge graph: ${this.nodes.size} nodes, ${this.edges.size} edges`,
      );
    } catch (err) {
      log.error(`Failed to load knowledge graph: ${err}`);
    }
  }

  addNode(
    node: Omit<KnowledgeNode, "id" | "createdAt" | "updatedAt" | "accessCount">,
    embedding?: number[],
  ): string {
    const id = randomUUID();
    const now = new Date().toISOString();
    const full: KnowledgeNode = {
      ...node,
      id,
      createdAt: now,
      updatedAt: now,
      accessCount: 0,
      ...(embedding ? { embedding } : {}),
    };
    this.nodes.set(id, full);
    log.debug(`Added node: ${full.title} (${id})`);
    return id;
  }

  addEdge(
    from: string,
    to: string,
    type: EdgeType,
    weight = 0.5,
    evidence?: string,
  ): string {
    if (!this.nodes.has(from)) throw new Error(`Node not found: ${from}`);
    if (!this.nodes.has(to)) throw new Error(`Node not found: ${to}`);

    const id = randomUUID();
    const edge: KnowledgeEdge = {
      id,
      from,
      to,
      type,
      weight,
      evidence,
      createdAt: new Date().toISOString(),
    };
    this.edges.set(id, edge);
    log.debug(`Added edge: ${from} --${type}--> ${to}`);
    return id;
  }

  findByDomain(domain: string): KnowledgeNode[] {
    const lowerDomain = domain.toLowerCase();
    return Array.from(this.nodes.values()).filter(
      (n) => n.domain.toLowerCase() === lowerDomain,
    );
  }

  search(query: string, limit = 10): KnowledgeNode[] {
    const lowerQuery = query.toLowerCase();
    const terms = lowerQuery.split(/\s+/).filter(Boolean);

    const scored: { node: KnowledgeNode; score: number }[] = [];
    for (const node of this.nodes.values()) {
      const haystack = `${node.title} ${node.content}`.toLowerCase();
      let score = 0;
      for (const term of terms) {
        const idx = haystack.indexOf(term);
        if (idx !== -1) {
          score += 1;
          if (node.title.toLowerCase().includes(term)) {
            score += 0.5;
          }
        }
      }
      if (score > 0) {
        score = score / terms.length;
        scored.push({ node, score });
      }
    }

    scored.sort((a, b) => b.score - a.score);
    return scored.slice(0, limit).map((s) => {
      s.node.accessCount++;
      s.node.updatedAt = new Date().toISOString();
      return s.node;
    });
  }

  async semanticSearch(
    query: string,
    limit = 10,
    embedder?: (text: string) => Promise<number[]>,
  ): Promise<KnowledgeNode[]> {
    const nodesWithEmbed = [...this.nodes.values()].filter(
      (n) => n.embedding?.length,
    );
    if (nodesWithEmbed.length === 0) {
      return this.search(query, limit);
    }

    if (!embedder) {
      return this.search(query, limit);
    }

    let queryEmbedding: number[] = [];
    try {
      queryEmbedding = await embedder(query);
    } catch {
      return this.search(query, limit);
    }

    if (queryEmbedding.length === 0) {
      return this.search(query, limit);
    }

    const scored = nodesWithEmbed.map((node) => ({
      node,
      score: this.cosineSimilarity(queryEmbedding, node.embedding!),
    }));

    const keywordResults = this.search(query, limit);
    const kwIds = new Set(keywordResults.map((n) => n.id));

    const hybrid = scored.map(({ node, score }) => ({
      node,
      score: kwIds.has(node.id) ? score + 0.1 : score,
    }));

    hybrid.sort((a, b) => b.score - a.score);
    return hybrid.slice(0, limit).map((r) => {
      r.node.accessCount++;
      r.node.updatedAt = new Date().toISOString();
      return r.node;
    });
  }

  private cosineSimilarity(a: number[], b: number[]): number {
    let dot = 0;
    let normA = 0;
    let normB = 0;
    for (let i = 0; i < Math.min(a.length, b.length); i++) {
      dot += a[i] * b[i];
      normA += a[i] * a[i];
      normB += b[i] * b[i];
    }
    const denom = Math.sqrt(normA) * Math.sqrt(normB);
    return denom === 0 ? 0 : dot / denom;
  }

  getEdges(nodeId: string): KnowledgeEdge[] {
    return Array.from(this.edges.values()).filter(
      (e) => e.from === nodeId || e.to === nodeId,
    );
  }

  getNeighbors(nodeId: string, edgeType?: EdgeType): KnowledgeNode[] {
    const relevantEdges = this.getEdges(nodeId).filter(
      (e) => !edgeType || e.type === edgeType,
    );
    const neighborIds = new Set<string>();
    for (const edge of relevantEdges) {
      neighborIds.add(edge.from === nodeId ? edge.to : edge.from);
    }
    return Array.from(neighborIds)
      .map((id) => this.nodes.get(id))
      .filter((n): n is KnowledgeNode => n !== undefined);
  }

  findContradictions(): {
    nodeA: KnowledgeNode;
    nodeB: KnowledgeNode;
    edge: KnowledgeEdge;
  }[] {
    const results: {
      nodeA: KnowledgeNode;
      nodeB: KnowledgeNode;
      edge: KnowledgeEdge;
    }[] = [];
    for (const edge of this.edges.values()) {
      if (edge.type !== "contradicts") continue;
      const nodeA = this.nodes.get(edge.from);
      const nodeB = this.nodes.get(edge.to);
      if (nodeA && nodeB) {
        results.push({ nodeA, nodeB, edge });
      }
    }
    return results;
  }

  getStats(): GraphStats {
    const nodes = Array.from(this.nodes.values());
    const domains = [...new Set(nodes.map((n) => n.domain))];
    const avgConfidence =
      nodes.length > 0
        ? nodes.reduce((sum, n) => sum + n.confidence, 0) / nodes.length
        : 0;

    const topNodes = [...nodes]
      .sort((a, b) => b.accessCount - a.accessCount)
      .slice(0, 10)
      .map((n) => ({ id: n.id, title: n.title, accessCount: n.accessCount }));

    return {
      totalNodes: this.nodes.size,
      totalEdges: this.edges.size,
      domains,
      avgConfidence,
      topNodes,
    };
  }

  removeNode(nodeId: string): void {
    this.nodes.delete(nodeId);
    for (const [edgeId, edge] of this.edges) {
      if (edge.from === nodeId || edge.to === nodeId) {
        this.edges.delete(edgeId);
      }
    }
    log.debug(`Removed node ${nodeId} and its edges`);
  }

  mergeNodes(keepId: string, removeId: string): void {
    const keep = this.nodes.get(keepId);
    const remove = this.nodes.get(removeId);
    if (!keep || !remove) {
      throw new Error(
        `Cannot merge: node not found (keep=${keepId}, remove=${removeId})`,
      );
    }

    keep.content = `${keep.content}\n\n${remove.content}`;
    keep.confidence = Math.max(keep.confidence, remove.confidence);
    keep.accessCount += remove.accessCount;
    keep.updatedAt = new Date().toISOString();

    for (const [edgeId, edge] of this.edges) {
      if (edge.from === removeId || edge.to === removeId) {
        const newFrom = edge.from === removeId ? keepId : edge.from;
        const newTo = edge.to === removeId ? keepId : edge.to;
        if (newFrom === newTo) {
          this.edges.delete(edgeId);
        } else {
          edge.from = newFrom;
          edge.to = newTo;
        }
      }
    }

    this.nodes.delete(removeId);
    log.info(`Merged node ${removeId} into ${keepId}`);
  }

  getNode(id: string): KnowledgeNode | undefined {
    return this.nodes.get(id);
  }

  getAllNodes(): KnowledgeNode[] {
    return Array.from(this.nodes.values());
  }

  getAllEdges(): KnowledgeEdge[] {
    return Array.from(this.edges.values());
  }

  async save(): Promise<void> {
    try {
      const dir = join(this.workspacePath);
      if (!existsSync(dir)) {
        mkdirSync(dir, { recursive: true });
      }
      const data: GraphData = {
        nodes: Array.from(this.nodes.values()),
        edges: Array.from(this.edges.values()),
      };
      writeFileSync(this.filePath, JSON.stringify(data, null, 2), "utf-8");
      log.debug(
        `Saved knowledge graph: ${data.nodes.length} nodes, ${data.edges.length} edges`,
      );
    } catch (err) {
      log.error(`Failed to save knowledge graph: ${err}`);
    }
  }
}
