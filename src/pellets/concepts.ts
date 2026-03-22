/**
 * StackOwl — Concept Extraction
 *
 * Extracts meaningful concepts from pellet content using heuristics
 * (no NLP dependency). Builds an inverted index for fast concept → pellet lookup.
 *
 * Extraction rules:
 *   1. Markdown headers (## Topic Name)
 *   2. Backtick-wrapped terms (`kubernetes`, `BM25`)
 *   3. Capitalized phrases (2+ words: "Knowledge Graph", "API Gateway")
 *   4. Explicit tags from pellet frontmatter
 */

// ─── Types ──────────────────────────────────────────────────────

export interface ConceptIndex {
  /** concept → set of pellet IDs containing it */
  conceptToPellets: Map<string, Set<string>>;
  /** pellet ID → set of concepts extracted from it */
  pelletToConcepts: Map<string, Set<string>>;
}

// ─── Extraction ─────────────────────────────────────────────────

const STOPWORDS = new Set([
  'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
  'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
  'should', 'may', 'might', 'shall', 'can', 'need', 'must', 'not',
  'and', 'or', 'but', 'if', 'then', 'else', 'when', 'how', 'what',
  'which', 'who', 'whom', 'this', 'that', 'these', 'those', 'here',
  'there', 'where', 'why', 'all', 'each', 'every', 'both', 'few',
  'more', 'most', 'some', 'any', 'no', 'only', 'own', 'same', 'so',
  'than', 'too', 'very', 'just', 'also', 'still', 'already', 'about',
  'new', 'old', 'first', 'last', 'next', 'many', 'much', 'well',
  'answer', 'example', 'related', 'json', 'topic', 'knowledge',
]);

/**
 * Extract concepts from a pellet's title + content + tags.
 * Returns a deduplicated, normalized set of concept strings.
 */
export function extractConcepts(
  title: string,
  content: string,
  tags: string[],
): string[] {
  const concepts = new Set<string>();

  // 1. Tags (already curated)
  for (const tag of tags) {
    const norm = tag.trim().toLowerCase();
    if (norm.length >= 2 && !STOPWORDS.has(norm)) {
      concepts.add(norm);
    }
  }

  // 2. Title words (high signal)
  const titleWords = title
    .toLowerCase()
    .split(/[^a-z0-9-]+/)
    .filter(w => w.length >= 3 && !STOPWORDS.has(w));
  for (const w of titleWords) {
    concepts.add(w);
  }

  // 3. Markdown headers: ## Heading Text
  const headerRe = /^#{1,4}\s+(.+)$/gm;
  let match: RegExpExecArray | null;
  while ((match = headerRe.exec(content)) !== null) {
    const heading = match[1].trim().toLowerCase().replace(/[^a-z0-9 -]/g, '');
    if (heading.length >= 3) {
      concepts.add(heading);
      // Also add individual words from the heading
      for (const w of heading.split(/\s+/)) {
        if (w.length >= 3 && !STOPWORDS.has(w)) concepts.add(w);
      }
    }
  }

  // 4. Backtick-wrapped terms: `term`
  const backtickRe = /`([^`]{2,40})`/g;
  while ((match = backtickRe.exec(content)) !== null) {
    const term = match[1].trim().toLowerCase();
    if (term.length >= 2 && !STOPWORDS.has(term) && !/^\d+$/.test(term)) {
      concepts.add(term);
    }
  }

  // 5. Capitalized phrases: "Knowledge Graph", "API Gateway"
  //    Match 2+ consecutive capitalized words
  const capRe = /\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b/g;
  while ((match = capRe.exec(content)) !== null) {
    const phrase = match[1].toLowerCase();
    if (phrase.length >= 5) {
      concepts.add(phrase);
    }
  }

  // 6. All-caps acronyms: API, BM25, TF-IDF
  const acronymRe = /\b([A-Z][A-Z0-9]{1,8})\b/g;
  while ((match = acronymRe.exec(content)) !== null) {
    const acronym = match[1].toLowerCase();
    if (acronym.length >= 2 && !STOPWORDS.has(acronym)) {
      concepts.add(acronym);
    }
  }

  return [...concepts];
}

// ─── Inverted Index ─────────────────────────────────────────────

export function createConceptIndex(): ConceptIndex {
  return {
    conceptToPellets: new Map(),
    pelletToConcepts: new Map(),
  };
}

/**
 * Add a pellet's concepts to the index.
 */
export function indexPellet(
  index: ConceptIndex,
  pelletId: string,
  concepts: string[],
): void {
  const conceptSet = new Set(concepts);
  index.pelletToConcepts.set(pelletId, conceptSet);

  for (const concept of concepts) {
    let pellets = index.conceptToPellets.get(concept);
    if (!pellets) {
      pellets = new Set();
      index.conceptToPellets.set(concept, pellets);
    }
    pellets.add(pelletId);
  }
}

/**
 * Remove a pellet from the index.
 */
export function removePelletFromIndex(
  index: ConceptIndex,
  pelletId: string,
): void {
  const concepts = index.pelletToConcepts.get(pelletId);
  if (!concepts) return;

  for (const concept of concepts) {
    const pellets = index.conceptToPellets.get(concept);
    if (pellets) {
      pellets.delete(pelletId);
      if (pellets.size === 0) {
        index.conceptToPellets.delete(concept);
      }
    }
  }
  index.pelletToConcepts.delete(pelletId);
}

/**
 * Find pellets that share concepts with the given pellet.
 * Returns pellet IDs sorted by concept overlap count (descending).
 */
export function findRelatedByConcepts(
  index: ConceptIndex,
  pelletId: string,
  limit = 10,
): Array<{ id: string; sharedConcepts: number }> {
  const concepts = index.pelletToConcepts.get(pelletId);
  if (!concepts) return [];

  const scores = new Map<string, number>();

  for (const concept of concepts) {
    const relatedPellets = index.conceptToPellets.get(concept);
    if (!relatedPellets) continue;
    for (const relId of relatedPellets) {
      if (relId === pelletId) continue;
      scores.set(relId, (scores.get(relId) ?? 0) + 1);
    }
  }

  return [...scores.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, limit)
    .map(([id, sharedConcepts]) => ({ id, sharedConcepts }));
}
