/**
 * StackOwl — Local TF-IDF / BM25 Search Engine
 *
 * Pure TypeScript, zero dependencies.  Works offline without any embedding
 * model.  Replaces the broken embedding-based search with a proper BM25
 * ranked retrieval engine that handles field boosting (title > tags > content),
 * stopword removal, and incremental index updates.
 *
 * Designed for up to 2000 pellets — fast enough that search feels instant.
 */

import { readFile, writeFile, rename } from "node:fs/promises";
import { existsSync } from "node:fs";

// ─── Types ────────────────────────────────────────────────────────

interface DocEntry {
  /** Term → raw count per field */
  title: Record<string, number>;
  tags: Record<string, number>;
  content: Record<string, number>;
  /** Token count per field (for BM25 length normalization) */
  lengths: [number, number, number]; // [title, tags, content]
}

interface SerializedIndex {
  version: 2;
  docCount: number;
  /** term → document frequency (number of docs containing the term) */
  df: Record<string, number>;
  docs: Record<string, DocEntry>;
  avgLengths: [number, number, number];
}

// ─── Constants ────────────────────────────────────────────────────

/** BM25 tuning parameters */
const K1 = 1.2;
const B = 0.75;

/** Field boost weights */
const BOOST_TITLE = 3.0;
const BOOST_TAGS = 2.0;
const BOOST_CONTENT = 1.0;

// prettier-ignore
const STOPWORDS = new Set([
  "a","about","above","after","again","against","all","am","an","and","any",
  "are","aren","as","at","be","because","been","before","being","below",
  "between","both","but","by","can","could","d","did","do","does","doing",
  "don","down","during","each","few","for","from","further","get","got",
  "had","has","have","having","he","her","here","hers","herself","him",
  "himself","his","how","i","if","in","into","is","isn","it","its","itself",
  "just","ll","m","me","might","more","most","must","my","myself","need",
  "no","nor","not","now","of","off","on","once","only","or","other","our",
  "ours","ourselves","out","over","own","re","s","same","shall","she",
  "should","so","some","such","t","than","that","the","their","theirs",
  "them","themselves","then","there","these","they","this","those","through",
  "to","too","under","until","up","ve","very","was","wasn","we","were",
  "weren","what","when","where","which","while","who","whom","why","will",
  "with","won","would","wouldn","you","your","yours","yourself","yourselves",
  "also","still","already","however","although","whether","since","yet",
  "always","never","often","sometimes","usually","rather","quite","really",
  "much","many","well","even","back","still","way","use","used","using",
]);

// ─── TfIdfEngine ──────────────────────────────────────────────────

export class TfIdfEngine {
  private docs: Record<string, DocEntry> = {};
  private df: Record<string, number> = {};
  private docCount = 0;
  private avgLengths: [number, number, number] = [0, 0, 0];

  constructor(private indexPath: string) {}

  // ─── Persistence ────────────────────────────────────────────────

  async load(): Promise<void> {
    if (!existsSync(this.indexPath)) return;
    try {
      const raw = await readFile(this.indexPath, "utf-8");
      const idx: SerializedIndex = JSON.parse(raw);
      if (idx.version !== 2) return; // incompatible — will rebuild
      this.docs = idx.docs;
      this.df = idx.df;
      this.docCount = idx.docCount;
      this.avgLengths = idx.avgLengths;
    } catch {
      // Corrupt — will rebuild on next addDocument
    }
  }

  async persist(): Promise<void> {
    const data: SerializedIndex = {
      version: 2,
      docCount: this.docCount,
      df: this.df,
      docs: this.docs,
      avgLengths: this.avgLengths,
    };
    const json = JSON.stringify(data);
    const tmp = this.indexPath + ".tmp";
    await writeFile(tmp, json, "utf-8");
    await rename(tmp, this.indexPath);
  }

  isEmpty(): boolean {
    return this.docCount === 0;
  }

  // ─── Index Mutation ─────────────────────────────────────────────

  addDocument(
    id: string,
    fields: { title: string; tags: string; content: string },
  ): void {
    // Upsert: remove old version first
    if (this.docs[id]) {
      this.removeDocument(id);
    }

    const titleTokens = tokenize(fields.title);
    const tagsTokens = tokenize(fields.tags);
    const contentTokens = tokenize(fields.content);

    const entry: DocEntry = {
      title: termFreqs(titleTokens),
      tags: termFreqs(tagsTokens),
      content: termFreqs(contentTokens),
      lengths: [titleTokens.length, tagsTokens.length, contentTokens.length],
    };
    this.docs[id] = entry;
    this.docCount++;

    // Update DF for every unique term in this document
    const allTerms = new Set([
      ...Object.keys(entry.title),
      ...Object.keys(entry.tags),
      ...Object.keys(entry.content),
    ]);
    for (const term of allTerms) {
      this.df[term] = (this.df[term] ?? 0) + 1;
    }

    this.recalcAvgLengths();
  }

  removeDocument(id: string): void {
    const entry = this.docs[id];
    if (!entry) return;

    // Decrement DF for every term in the removed document
    const allTerms = new Set([
      ...Object.keys(entry.title),
      ...Object.keys(entry.tags),
      ...Object.keys(entry.content),
    ]);
    for (const term of allTerms) {
      const newDf = (this.df[term] ?? 1) - 1;
      if (newDf <= 0) {
        delete this.df[term];
      } else {
        this.df[term] = newDf;
      }
    }

    delete this.docs[id];
    this.docCount = Math.max(0, this.docCount - 1);
    this.recalcAvgLengths();
  }

  // ─── Search ─────────────────────────────────────────────────────

  search(query: string, limit = 20): Array<{ id: string; score: number }> {
    const queryTokens = tokenize(query);
    if (queryTokens.length === 0 || this.docCount === 0) return [];

    const results: Array<{ id: string; score: number }> = [];

    for (const [id, doc] of Object.entries(this.docs)) {
      let score = 0;

      for (const term of queryTokens) {
        const idf = this.idf(term);
        if (idf <= 0) continue;

        // BM25 per field with boost
        score +=
          BOOST_TITLE *
          bm25(doc.title[term] ?? 0, idf, doc.lengths[0], this.avgLengths[0]);
        score +=
          BOOST_TAGS *
          bm25(doc.tags[term] ?? 0, idf, doc.lengths[1], this.avgLengths[1]);
        score +=
          BOOST_CONTENT *
          bm25(
            doc.content[term] ?? 0,
            idf,
            doc.lengths[2],
            this.avgLengths[2],
          );
      }

      if (score > 0) {
        results.push({ id, score });
      }
    }

    results.sort((a, b) => b.score - a.score);
    return results.slice(0, limit);
  }

  /**
   * Compute BM25 self-score for a document against its own content.
   * Used by PelletDeduplicator to normalize similarity into a 0-1 range.
   *
   * Temporarily indexes the document, queries it, then removes it —
   * the real index is left unchanged.
   */
  selfScore(fields: { title: string; tags: string; content: string }): number {
    const tempId = `__self_score_${Date.now()}`;
    this.addDocument(tempId, fields);
    const query = `${fields.title} ${fields.content.slice(0, 200)}`;
    const results = this.search(query, 1);
    const score = results.find((r) => r.id === tempId)?.score ?? 0;
    this.removeDocument(tempId);
    return score;
  }

  // ─── Private ────────────────────────────────────────────────────

  private idf(term: string): number {
    const df = this.df[term] ?? 0;
    if (df === 0) return 0;
    // BM25 IDF variant — avoids negative values
    return Math.log((this.docCount - df + 0.5) / (df + 0.5) + 1);
  }

  private recalcAvgLengths(): void {
    if (this.docCount === 0) {
      this.avgLengths = [0, 0, 0];
      return;
    }
    let t = 0,
      g = 0,
      c = 0;
    for (const doc of Object.values(this.docs)) {
      t += doc.lengths[0];
      g += doc.lengths[1];
      c += doc.lengths[2];
    }
    this.avgLengths = [
      t / this.docCount,
      g / this.docCount,
      c / this.docCount,
    ];
  }
}

// ─── Helpers (module-level for tree-shaking) ──────────────────────

function tokenize(text: string): string[] {
  return text
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter((w) => w.length >= 2 && !STOPWORDS.has(w));
}

function termFreqs(tokens: string[]): Record<string, number> {
  const freq: Record<string, number> = {};
  for (const t of tokens) {
    freq[t] = (freq[t] ?? 0) + 1;
  }
  return freq;
}

function bm25(
  tf: number,
  idf: number,
  fieldLen: number,
  avgFieldLen: number,
): number {
  if (tf === 0 || avgFieldLen === 0) return 0;
  return idf * ((tf * (K1 + 1)) / (tf + K1 * (1 - B + B * (fieldLen / avgFieldLen))));
}
