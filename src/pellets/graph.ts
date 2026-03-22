/**
 * StackOwl — Pellet Knowledge Graph
 *
 * In-memory graph linking pellets together via:
 *   1. Tag co-occurrence (pellets sharing 2+ tags)
 *   2. Concept overlap (extracted concepts from content)
 *   3. BM25 cross-similarity (content similarity above threshold)
 *
 * Provides:
 *   - N-hop traversal: "find everything related to X"
 *   - Topic clustering: connected components = knowledge clusters
 *   - Enhanced dedup signals: concept overlap as additional similarity
 *
 * Built on graphology (~15KB, zero native deps).
 */

import Graph from 'graphology';
import { bfsFromNode } from 'graphology-traversal';
import { connectedComponents } from 'graphology-components';
import type { Pellet, PelletStore } from './store.js';
import type { TfIdfEngine } from './tfidf.js';
import {
  extractConcepts,
  createConceptIndex,
  indexPellet,
  removePelletFromIndex,
  findRelatedByConcepts,
  type ConceptIndex,
} from './concepts.js';
import { log } from '../logger.js';

// ─── Types ──────────────────────────────────────────────────────

interface PelletNodeAttrs {
  title: string;
  tags: string[];
  concepts: string[];
}

interface EdgeAttrs {
  /** Sum of all relationship signals */
  weight: number;
  /** Why this edge exists */
  sources: ('tags' | 'concepts' | 'bm25')[];
}

export interface RelatedPellet {
  id: string;
  title: string;
  weight: number;
  hops: number;
  sources: string[];
}

export interface KnowledgeCluster {
  id: number;
  pelletIds: string[];
  topTags: string[];
  size: number;
}

// ─── Constants ──────────────────────────────────────────────────

/** Minimum shared tags to create a tag-based edge */
const MIN_TAG_OVERLAP = 2;
/** Weight per shared tag */
const TAG_WEIGHT = 1.0;
/** Weight per shared concept */
const CONCEPT_WEIGHT = 0.5;
/** Minimum BM25 normalized similarity to create an edge */
const BM25_EDGE_THRESHOLD = 0.3;
/** Weight for BM25 similarity edge */
const BM25_WEIGHT = 2.0;
/** Max BM25 candidates to check per pellet during build */
const BM25_CANDIDATES = 5;

// ─── PelletGraph ────────────────────────────────────────────────

export class PelletGraph {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  private graph: any;
  private conceptIndex: ConceptIndex;
  private built = false;

  constructor(
    private pelletStore: PelletStore,
    private tfidf: TfIdfEngine,
  ) {
    // Resolve CJS/ESM default export
    const G = (typeof Graph === 'function' ? Graph : (Graph as any).default) as any;
    this.graph = new G({ type: 'undirected', allowSelfLoops: false });
    this.conceptIndex = createConceptIndex();
  }

  /**
   * Build the full graph from all pellets on disk.
   * Call once at startup, then use addPellet/removePellet for incremental updates.
   */
  async build(): Promise<void> {
    const start = Date.now();
    const pellets = await this.pelletStore.listAll();

    // Phase 1: Add all nodes + extract concepts
    for (const pellet of pellets) {
      this.addNode(pellet);
    }

    // Phase 2: Create edges
    this.buildTagEdges(pellets);
    this.buildConceptEdges(pellets);
    this.buildBm25Edges(pellets);

    this.built = true;
    const elapsed = Date.now() - start;
    log.pellet.info(
      `[PelletGraph] Built: ${this.graph.order} nodes, ${this.graph.size} edges in ${elapsed}ms`,
    );
  }

  /**
   * Add or update a single pellet in the graph (incremental).
   */
  addPellet(pellet: Pellet): void {
    // Remove old version if exists
    if (this.graph.hasNode(pellet.id)) {
      this.removePellet(pellet.id);
    }

    this.addNode(pellet);

    // Re-check edges against all existing nodes
    this.graph.forEachNode((otherId: string, other: PelletNodeAttrs) => {
      if (otherId === pellet.id) return;
      this.maybeCreateEdge(pellet.id, otherId, pellet.tags, other.tags);
    });
  }

  /**
   * Remove a pellet from the graph.
   */
  removePellet(pelletId: string): void {
    if (this.graph.hasNode(pelletId)) {
      this.graph.dropNode(pelletId);
    }
    removePelletFromIndex(this.conceptIndex, pelletId);
  }

  /**
   * Find related pellets using N-hop BFS traversal.
   * Returns pellets ranked by edge weight and hop distance.
   */
  findRelated(pelletId: string, maxHops = 2, limit = 10): RelatedPellet[] {
    if (!this.graph.hasNode(pelletId)) return [];

    const results: RelatedPellet[] = [];
    const visited = new Set<string>();

    bfsFromNode(this.graph, pelletId, (node: string, attrs: any, depth: number) => {
      if (depth > maxHops) return true; // stop traversal
      if (node === pelletId) return false;
      if (visited.has(node)) return false;
      visited.add(node);

      // Get edge weight to the source or accumulated
      let weight = 0;
      let sources: string[] = [];

      // Check direct edge first
      const edgeKey = this.graph.hasEdge(pelletId, node)
        ? this.graph.edge(pelletId, node)
        : undefined;

      if (edgeKey != null) {
        const edgeAttrs = this.graph.getEdgeAttributes(edgeKey) as EdgeAttrs;
        weight = edgeAttrs.weight;
        sources = edgeAttrs.sources;
      } else {
        // Multi-hop — use concept overlap as proxy weight
        const conceptRelated = findRelatedByConcepts(this.conceptIndex, pelletId, 1);
        const match = conceptRelated.find(r => r.id === node);
        weight = match ? match.sharedConcepts * CONCEPT_WEIGHT : 0.1;
        sources = ['indirect'];
      }

      // Discount by hop distance
      const discountedWeight = weight / depth;

      results.push({
        id: node,
        title: (attrs as PelletNodeAttrs).title ?? node,
        weight: discountedWeight,
        hops: depth,
        sources,
      });
      return false;
    });

    // Sort by weight descending
    results.sort((a, b) => b.weight - a.weight);
    return results.slice(0, limit);
  }

  /**
   * Find related pellets by query — combines BM25 search with graph traversal.
   * First finds top BM25 matches, then expands via graph neighbors.
   */
  findRelatedByQuery(query: string, limit = 10): RelatedPellet[] {
    const bm25Results = this.tfidf.search(query, 3);
    if (bm25Results.length === 0) return [];

    const allRelated = new Map<string, RelatedPellet>();

    for (const { id } of bm25Results) {
      const neighbors = this.findRelated(id, 2, limit);
      for (const neighbor of neighbors) {
        const existing = allRelated.get(neighbor.id);
        if (!existing || neighbor.weight > existing.weight) {
          allRelated.set(neighbor.id, neighbor);
        }
      }
      // Include the BM25 match itself
      if (!allRelated.has(id) && this.graph.hasNode(id)) {
        const attrs = this.graph.getNodeAttributes(id) as PelletNodeAttrs;
        allRelated.set(id, {
          id,
          title: attrs.title,
          weight: 10, // High weight for direct BM25 matches
          hops: 0,
          sources: ['bm25-direct'],
        });
      }
    }

    return [...allRelated.values()]
      .sort((a, b) => b.weight - a.weight)
      .slice(0, limit);
  }

  /**
   * Get topic clusters — groups of related pellets.
   * Uses connected components from the graph.
   */
  getClusters(): KnowledgeCluster[] {
    const components = connectedComponents(this.graph);
    const clusters: KnowledgeCluster[] = [];

    for (let i = 0; i < components.length; i++) {
      const pelletIds = components[i];
      if (pelletIds.length < 2) continue; // Skip isolated nodes

      // Collect all tags from the cluster
      const tagCounts = new Map<string, number>();
      for (const id of pelletIds) {
        if (!this.graph.hasNode(id)) continue;
        const attrs = this.graph.getNodeAttributes(id) as PelletNodeAttrs;
        for (const tag of attrs.tags) {
          tagCounts.set(tag, (tagCounts.get(tag) ?? 0) + 1);
        }
      }

      // Top tags by frequency
      const topTags = [...tagCounts.entries()]
        .sort((a, b) => b[1] - a[1])
        .slice(0, 5)
        .map(([tag]) => tag);

      clusters.push({
        id: i,
        pelletIds,
        topTags,
        size: pelletIds.length,
      });
    }

    // Sort by size descending
    clusters.sort((a, b) => b.size - a.size);
    return clusters;
  }

  /**
   * Get graph statistics.
   */
  getStats(): { nodes: number; edges: number; clusters: number; avgDegree: number } {
    const components = connectedComponents(this.graph);
    const multiNodeClusters = components.filter(c => c.length >= 2).length;
    const totalDegree = this.graph.order > 0
      ? [...this.graph.nodes()].reduce((sum, n) => sum + this.graph.degree(n), 0)
      : 0;
    const avgDegree = this.graph.order > 0 ? totalDegree / this.graph.order : 0;

    return {
      nodes: this.graph.order,
      edges: this.graph.size,
      clusters: multiNodeClusters,
      avgDegree: Math.round(avgDegree * 100) / 100,
    };
  }

  isBuilt(): boolean {
    return this.built;
  }

  // ─── Private: Node management ─────────────────────────────────

  private addNode(pellet: Pellet): void {
    const concepts = extractConcepts(pellet.title, pellet.content, pellet.tags);

    this.graph.mergeNode(pellet.id, {
      title: pellet.title,
      tags: pellet.tags,
      concepts,
    });

    indexPellet(this.conceptIndex, pellet.id, concepts);
  }

  // ─── Private: Edge builders ───────────────────────────────────

  private buildTagEdges(pellets: Pellet[]): void {
    // O(n^2) but fine for up to 2000 pellets
    for (let i = 0; i < pellets.length; i++) {
      for (let j = i + 1; j < pellets.length; j++) {
        this.maybeCreateEdge(
          pellets[i].id, pellets[j].id,
          pellets[i].tags, pellets[j].tags,
        );
      }
    }
  }

  private buildConceptEdges(pellets: Pellet[]): void {
    for (const pellet of pellets) {
      const related = findRelatedByConcepts(this.conceptIndex, pellet.id, 10);
      for (const { id: otherId, sharedConcepts } of related) {
        if (sharedConcepts < 3) continue; // Require meaningful overlap
        this.addOrUpdateEdge(pellet.id, otherId, sharedConcepts * CONCEPT_WEIGHT, 'concepts');
      }
    }
  }

  private buildBm25Edges(pellets: Pellet[]): void {
    for (const pellet of pellets) {
      const query = `${pellet.title} ${pellet.content.slice(0, 200)}`;
      const selfScore = this.tfidf.selfScore({
        title: pellet.title,
        tags: pellet.tags.join(' '),
        content: pellet.content,
      });

      if (selfScore <= 0) continue;

      const candidates = this.tfidf
        .search(query, BM25_CANDIDATES + 1)
        .filter(r => r.id !== pellet.id);

      for (const { id: otherId, score } of candidates) {
        const similarity = score / selfScore;
        if (similarity >= BM25_EDGE_THRESHOLD) {
          this.addOrUpdateEdge(pellet.id, otherId, similarity * BM25_WEIGHT, 'bm25');
        }
      }
    }
  }

  private maybeCreateEdge(
    id1: string, id2: string,
    tags1: string[], tags2: string[],
  ): void {
    const set1 = new Set(tags1.map(t => t.toLowerCase()));
    let overlap = 0;
    for (const tag of tags2) {
      if (set1.has(tag.toLowerCase())) overlap++;
    }
    if (overlap >= MIN_TAG_OVERLAP) {
      this.addOrUpdateEdge(id1, id2, overlap * TAG_WEIGHT, 'tags');
    }
  }

  private addOrUpdateEdge(
    id1: string, id2: string,
    weight: number,
    source: 'tags' | 'concepts' | 'bm25',
  ): void {
    if (!this.graph.hasNode(id1) || !this.graph.hasNode(id2)) return;

    if (this.graph.hasEdge(id1, id2)) {
      const edge = this.graph.edge(id1, id2)!;
      const attrs = this.graph.getEdgeAttributes(edge) as EdgeAttrs;
      attrs.weight += weight;
      if (!attrs.sources.includes(source)) {
        attrs.sources.push(source);
      }
      this.graph.setEdgeAttribute(edge, 'weight', attrs.weight);
      this.graph.setEdgeAttribute(edge, 'sources', attrs.sources);
    } else {
      this.graph.addEdge(id1, id2, { weight, sources: [source] });
    }
  }
}
