/**
 * StackOwl — Knowledge Council
 *
 * A scheduled gathering where all owls:
 * 1. Learn independently on their specialty topics
 * 2. Present what they learned to the group
 * 3. Challenge and validate each other's findings
 * 4. Synthesize cross-pollinated insights into high-confidence pellets
 *
 * This creates an organic learning ecosystem where owls push each other
 * to grow, question assumptions, and discover connections across domains.
 */

import { v4 as uuidv4 } from "uuid";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { join } from "node:path";
import type { ModelProvider } from "../providers/base.js";
import type { OwlInstance } from "../owls/persona.js";
import type { StackOwlConfig } from "../config/loader.js";
import type { PelletStore } from "../pellets/store.js";
import type { OwlRegistry } from "../owls/registry.js";
import type { ProviderRegistry } from "../providers/registry.js";
import { OwlInnerLife } from "../owls/inner-life.js";
import { PelletGenerator } from "../pellets/generator.js";
import { log } from "../logger.js";

// ─── Types ──────────────────────────────────────────────────────

export interface IndependentLearning {
  owlName: string;
  owlEmoji: string;
  topic: string;
  /** What the owl learned — a concise summary */
  findings: string;
  /** Key insights the owl wants to share */
  keyInsights: string[];
  /** Questions the owl still has */
  openQuestions: string[];
  /** Confidence in their findings (0–1) */
  confidence: number;
}

export interface PeerReview {
  reviewerName: string;
  reviewerEmoji: string;
  targetOwl: string;
  /** Agreement, challenge, or expansion */
  type: "agree" | "challenge" | "expand";
  /** The reviewer's feedback */
  feedback: string;
  /** Specific points challenged or validated */
  points: string[];
  /** Does the reviewer think this is reliable knowledge? */
  trustScore: number;
}

export interface CrossPollination {
  /** Connection discovered between two owls' learnings */
  connection: string;
  /** Which owls' work is connected */
  owls: string[];
  /** A new insight that emerged from combining their knowledge */
  emergentInsight: string;
}

export interface CouncilSession {
  id: string;
  startedAt: string;
  completedAt?: string;
  phase:
    | "independent"
    | "presenting"
    | "reviewing"
    | "synthesizing"
    | "complete";
  /** Each owl's independent learning */
  learnings: IndependentLearning[];
  /** Peer reviews from cross-examination */
  reviews: PeerReview[];
  /** Cross-domain connections discovered */
  crossPollinations: CrossPollination[];
  /** Pellets created from validated knowledge */
  pelletsCreated: number;
  /** Summary of the session */
  summary?: string;
}

export interface CouncilHistory {
  sessions: {
    id: string;
    date: string;
    topics: string[];
    pelletsCreated: number;
    participantCount: number;
  }[];
  /** Topics that have been studied — avoid repeating */
  studiedTopics: string[];
  /** Topics suggested for future councils */
  suggestedTopics: string[];
  lastCouncil?: string;
}

// ─── Knowledge Council ─────────────────────────────────────────

export class KnowledgeCouncil {
  private history: CouncilHistory | null = null;
  private historyPath: string;
  private pelletGenerator: PelletGenerator;

  constructor(
    private provider: ModelProvider,
    private owlRegistry: OwlRegistry,
    private config: StackOwlConfig,
    private pelletStore: PelletStore,
    private workspacePath: string,
    _providerRegistry?: ProviderRegistry,
  ) {
    this.historyPath = join(workspacePath, "council_history.json");
    this.pelletGenerator = new PelletGenerator();
  }

  /**
   * Run a full Knowledge Council session.
   *
   * @param topics — Optional specific topics. If not provided, each owl picks
   *                 a topic based on its specialties, desires, and knowledge gaps.
   * @param onProgress — Callback for live updates (e.g., streaming to Telegram)
   */
  async convene(
    topics?: string[],
    onProgress?: (msg: string) => Promise<void>,
  ): Promise<CouncilSession> {
    await this.loadHistory();

    const allOwls = this.owlRegistry.listOwls();
    if (allOwls.length < 2) {
      throw new Error("Knowledge Council requires at least 2 owls.");
    }

    const session: CouncilSession = {
      id: uuidv4(),
      startedAt: new Date().toISOString(),
      phase: "independent",
      learnings: [],
      reviews: [],
      crossPollinations: [],
      pelletsCreated: 0,
    };

    log.engine.info(`[KnowledgeCouncil] Convening with ${allOwls.length} owls`);
    await onProgress?.(
      `🏛️ **Knowledge Council** convening — ${allOwls.length} owls gathering...`,
    );

    // Phase 1: Independent Learning
    await onProgress?.(
      `📚 **Phase 1: Independent Study** — Each owl is researching their topic...`,
    );
    await this.phaseIndependentLearning(session, allOwls, topics, onProgress);

    // Phase 2: Presentations & Peer Review
    await onProgress?.(
      `🔍 **Phase 2: Peer Review** — Owls are challenging each other's findings...`,
    );
    session.phase = "reviewing";
    await this.phasePeerReview(session, allOwls, onProgress);

    // Phase 3: Cross-Pollination
    await onProgress?.(
      `🔗 **Phase 3: Cross-Pollination** — Looking for connections across domains...`,
    );
    session.phase = "synthesizing";
    await this.phaseCrossPollination(session, allOwls, onProgress);

    // Phase 4: Create validated pellets
    await this.phaseCreatePellets(session, allOwls, onProgress);

    // Update inner life for each owl
    await this.updateOwlInnerLives(session, allOwls);

    session.phase = "complete";
    session.completedAt = new Date().toISOString();

    // Generate session summary
    session.summary = await this.generateSummary(session);

    // Save to history
    await this.saveToHistory(session);

    log.engine.info(
      `[KnowledgeCouncil] Session complete — ${session.pelletsCreated} pellets created, ` +
        `${session.crossPollinations.length} cross-domain connections found`,
    );

    await onProgress?.(
      `✅ **Knowledge Council complete!**\n\n` + `${session.summary}`,
    );

    return session;
  }

  // ─── Phase 1: Independent Learning ──────────────────────────────

  private async phaseIndependentLearning(
    session: CouncilSession,
    owls: OwlInstance[],
    topics?: string[],
    onProgress?: (msg: string) => Promise<void>,
  ): Promise<void> {
    // Assign topics to owls — either from input or auto-selected
    const owlTopics = await this.assignTopics(owls, topics);

    // Run all owls' independent research in parallel
    const learningPromises = owls.map(async (owl, i) => {
      const topic = owlTopics[i];
      await onProgress?.(
        `  ${owl.persona.emoji} **${owl.persona.name}** is studying: *${topic}*`,
      );

      try {
        const learning = await this.owlIndependentStudy(owl, topic);
        session.learnings.push(learning);
        await onProgress?.(
          `  ${owl.persona.emoji} **${owl.persona.name}** finished — ` +
            `${learning.keyInsights.length} insights, ${(learning.confidence * 100).toFixed(0)}% confidence`,
        );
      } catch (err) {
        log.engine.warn(
          `[KnowledgeCouncil] ${owl.persona.name} study failed: ${err instanceof Error ? err.message : err}`,
        );
      }
    });

    await Promise.allSettled(learningPromises);
  }

  private async assignTopics(
    owls: OwlInstance[],
    explicitTopics?: string[],
  ): Promise<string[]> {
    if (explicitTopics && explicitTopics.length >= owls.length) {
      return explicitTopics.slice(0, owls.length);
    }

    const history = this.history!;
    const recentlyStudied = new Set(history.studiedTopics.slice(-20));

    // Each owl picks a topic based on its specialties + inner curiosities
    const topicPromises = owls.map(async (owl) => {
      // Try to load inner state for desire-driven topic selection
      let desires: string[] = [];
      try {
        const innerLife = new OwlInnerLife(
          this.provider,
          owl,
          this.workspacePath,
        );
        await innerLife.load();
        const state = JSON.parse(
          await readFile(
            join(
              this.workspacePath,
              "owls",
              owl.persona.name.toLowerCase(),
              "inner_state.json",
            ),
            "utf-8",
          ).catch(() => "{}"),
        );
        if (state.desires) {
          desires = state.desires
            .filter((d: { intensity: number }) => d.intensity > 0.4)
            .map((d: { description: string }) => d.description);
        }
      } catch {
        /* no inner state yet */
      }

      const prompt = `You are ${owl.persona.name} (${owl.persona.type}).
Your specialties: ${owl.persona.specialties.join(", ")}
${desires.length > 0 ? `Your current curiosities: ${desires.join("; ")}` : ""}
Topics already studied recently (AVOID THESE): ${[...recentlyStudied].join(", ") || "none"}
${history.suggestedTopics.length > 0 ? `Topics suggested by peers: ${history.suggestedTopics.join(", ")}` : ""}

Pick ONE specific topic you want to study right now. It should:
1. Be within or adjacent to your specialties
2. Be specific enough to research in one session (not "AI" but "how transformer attention heads specialize during fine-tuning")
3. Be something you're genuinely curious about
4. NOT overlap with recently studied topics

Respond with ONLY the topic — no explanation, no quotes. Just the topic in one line.`;

      try {
        const response = await this.provider.chat(
          [
            { role: "system", content: prompt },
            { role: "user", content: "What do you want to study?" },
          ],
          undefined,
          { temperature: 0.9, maxTokens: 100 },
        );
        return response.content
          .replace(/<\/?(?:think|reasoning)>/gi, "")
          .trim()
          .split("\n")[0]
          .replace(/^["']|["']$/g, "")
          .trim();
      } catch {
        return `${owl.persona.specialties[0]} — latest developments`;
      }
    });

    return Promise.all(topicPromises);
  }

  private async owlIndependentStudy(
    owl: OwlInstance,
    topic: string,
  ): Promise<IndependentLearning> {
    const studyPrompt = `You are ${owl.persona.name} (${owl.persona.type}).
Your specialties: ${owl.persona.specialties.join(", ")}
Your traits: ${owl.persona.traits.join(", ")}

You are independently studying: "${topic}"

Research this topic thoroughly using your expertise. Think deeply — don't just summarize surface-level knowledge. Apply your unique perspective as a ${owl.persona.type}.

Respond as JSON:
{
  "findings": "A 3-5 sentence summary of what you learned, written from your perspective",
  "keyInsights": ["insight 1 — something non-obvious you discovered", "insight 2", "insight 3"],
  "openQuestions": ["question you still have after studying", "another question"],
  "confidence": 0.0-1.0
}`;

    const response = await this.provider.chat(
      [
        { role: "system", content: studyPrompt },
        { role: "user", content: `Study "${topic}" now.` },
      ],
      undefined,
      { temperature: 0.7, maxTokens: 800 },
    );

    const text = response.content
      .replace(/<\/?(?:think|reasoning)>/gi, "")
      .trim();

    const jsonMatch = text.match(/\{[\s\S]*\}/);
    if (!jsonMatch) {
      return {
        owlName: owl.persona.name,
        owlEmoji: owl.persona.emoji,
        topic,
        findings: text.slice(0, 500),
        keyInsights: [],
        openQuestions: [],
        confidence: 0.5,
      };
    }

    const parsed = JSON.parse(jsonMatch[0]);
    return {
      owlName: owl.persona.name,
      owlEmoji: owl.persona.emoji,
      topic,
      findings: parsed.findings ?? text.slice(0, 500),
      keyInsights: parsed.keyInsights ?? [],
      openQuestions: parsed.openQuestions ?? [],
      confidence: Math.min(1, Math.max(0, parsed.confidence ?? 0.5)),
    };
  }

  // ─── Phase 2: Peer Review ──────────────────────────────────────

  private async phasePeerReview(
    session: CouncilSession,
    owls: OwlInstance[],
    onProgress?: (msg: string) => Promise<void>,
  ): Promise<void> {
    // Each owl reviews every other owl's findings
    const reviewPromises: Promise<void>[] = [];

    for (const reviewer of owls) {
      for (const learning of session.learnings) {
        // Don't review yourself
        if (learning.owlName === reviewer.persona.name) continue;

        reviewPromises.push(
          this.owlReview(reviewer, learning)
            .then(async (review) => {
              session.reviews.push(review);
              if (review.type === "challenge") {
                await onProgress?.(
                  `  ${reviewer.persona.emoji} **${reviewer.persona.name}** challenges ` +
                    `${learning.owlEmoji} **${learning.owlName}** on *${learning.topic}*: ` +
                    `"${review.feedback.slice(0, 120)}..."`,
                );
              } else if (review.type === "expand") {
                await onProgress?.(
                  `  ${reviewer.persona.emoji} **${reviewer.persona.name}** expands on ` +
                    `${learning.owlEmoji} **${learning.owlName}**'s findings about *${learning.topic}*`,
                );
              }
            })
            .catch((err) => {
              log.engine.warn(
                `[KnowledgeCouncil] Review by ${reviewer.persona.name} failed: ${err instanceof Error ? err.message : err}`,
              );
            }),
        );
      }
    }

    await Promise.allSettled(reviewPromises);
  }

  private async owlReview(
    reviewer: OwlInstance,
    learning: IndependentLearning,
  ): Promise<PeerReview> {
    const reviewPrompt = `You are ${reviewer.persona.name} (${reviewer.persona.type}).
Your traits: ${reviewer.persona.traits.join(", ")}
Your specialties: ${reviewer.persona.specialties.join(", ")}
Challenge level: ${reviewer.dna.evolvedTraits.challengeLevel}

${learning.owlName} just presented their research on "${learning.topic}":

**Findings:** ${learning.findings}

**Key Insights:**
${learning.keyInsights.map((i, idx) => `${idx + 1}. ${i}`).join("\n")}

**Open Questions:**
${learning.openQuestions.map((q) => `- ${q}`).join("\n")}

**Their confidence:** ${(learning.confidence * 100).toFixed(0)}%

YOUR TASK: Review their work through YOUR lens as a ${reviewer.persona.type}. Be honest.
- If you're a devil's advocate, CHALLENGE their assumptions
- If you're an engineer, check for PRACTICAL gaps
- If you're a financial specialist, check the ECONOMIC angles
- Whatever your role, bring your unique perspective

Respond as JSON:
{
  "type": "agree" | "challenge" | "expand",
  "feedback": "2-3 sentences of honest peer feedback — be specific, not generic",
  "points": ["specific point 1 you're raising", "point 2"],
  "trustScore": 0.0-1.0
}`;

    const response = await this.provider.chat(
      [
        { role: "system", content: reviewPrompt },
        { role: "user", content: "Give your honest review." },
      ],
      undefined,
      { temperature: 0.8, maxTokens: 400 },
    );

    const text = response.content
      .replace(/<\/?(?:think|reasoning)>/gi, "")
      .trim();

    const jsonMatch = text.match(/\{[\s\S]*\}/);
    if (!jsonMatch) {
      return {
        reviewerName: reviewer.persona.name,
        reviewerEmoji: reviewer.persona.emoji,
        targetOwl: learning.owlName,
        type: "agree",
        feedback: "Interesting findings, I don't have specific objections.",
        points: [],
        trustScore: 0.6,
      };
    }

    const parsed = JSON.parse(jsonMatch[0]);
    return {
      reviewerName: reviewer.persona.name,
      reviewerEmoji: reviewer.persona.emoji,
      targetOwl: learning.owlName,
      type: parsed.type ?? "agree",
      feedback: parsed.feedback ?? "",
      points: parsed.points ?? [],
      trustScore: Math.min(1, Math.max(0, parsed.trustScore ?? 0.6)),
    };
  }

  // ─── Phase 3: Cross-Pollination ─────────────────────────────────

  private async phaseCrossPollination(
    session: CouncilSession,
    owls: OwlInstance[],
    onProgress?: (msg: string) => Promise<void>,
  ): Promise<void> {
    if (session.learnings.length < 2) return;

    // Use the most analytical/general owl to find connections
    const synthesizer =
      owls.find(
        (o) =>
          o.persona.type.includes("executive") ||
          o.persona.type.includes("assistant"),
      ) ?? owls[0];

    const allLearnings = session.learnings
      .map(
        (l) =>
          `**${l.owlName}** studied "${l.topic}":\n${l.findings}\nInsights: ${l.keyInsights.join("; ")}`,
      )
      .join("\n\n---\n\n");

    const allReviews = session.reviews
      .filter((r) => r.type !== "agree")
      .map(
        (r) => `${r.reviewerName} → ${r.targetOwl}: [${r.type}] ${r.feedback}`,
      )
      .join("\n");

    const crossPrompt = `You are ${synthesizer.persona.name}, facilitating a Knowledge Council.

All owls have completed their independent research and peer review. Now find the CONNECTIONS.

## Individual Learnings:
${allLearnings}

## Peer Review Highlights:
${allReviews || "No significant challenges raised."}

YOUR TASK: Find cross-domain connections that NO SINGLE OWL would have discovered alone.
Look for:
1. Shared patterns across different domains
2. How one owl's insight solves another owl's open question
3. Contradictions that reveal deeper truths
4. Emergent ideas from combining perspectives

Respond as JSON:
{
  "connections": [
    {
      "connection": "description of the cross-domain connection",
      "owls": ["owl1", "owl2"],
      "emergentInsight": "the NEW insight that emerges from combining their knowledge"
    }
  ],
  "suggestedTopics": ["topic for next council session based on what we learned"]
}`;

    try {
      const response = await this.provider.chat(
        [
          { role: "system", content: crossPrompt },
          { role: "user", content: "Find the connections." },
        ],
        undefined,
        { temperature: 0.8, maxTokens: 800 },
      );

      const text = response.content
        .replace(/<\/?(?:think|reasoning)>/gi, "")
        .trim();

      const jsonMatch = text.match(/\{[\s\S]*\}/);
      if (jsonMatch) {
        const parsed = JSON.parse(jsonMatch[0]);
        session.crossPollinations = parsed.connections ?? [];
        if (parsed.suggestedTopics) {
          this.history!.suggestedTopics = [
            ...parsed.suggestedTopics,
            ...this.history!.suggestedTopics,
          ].slice(0, 10);
        }

        for (const cp of session.crossPollinations) {
          await onProgress?.(
            `  🔗 Connection: ${cp.owls.join(" ↔ ")}: *${cp.emergentInsight.slice(0, 150)}*`,
          );
        }
      }
    } catch (err) {
      log.engine.warn(
        `[KnowledgeCouncil] Cross-pollination failed: ${err instanceof Error ? err.message : err}`,
      );
    }
  }

  // ─── Phase 4: Create Validated Pellets ──────────────────────────

  private async phaseCreatePellets(
    session: CouncilSession,
    owls: OwlInstance[],
    onProgress?: (msg: string) => Promise<void>,
  ): Promise<void> {
    // Create pellets for learnings that passed peer review
    for (const learning of session.learnings) {
      const reviews = session.reviews.filter(
        (r) => r.targetOwl === learning.owlName,
      );
      const avgTrust =
        reviews.length > 0
          ? reviews.reduce((sum, r) => sum + r.trustScore, 0) / reviews.length
          : learning.confidence;

      // Only create pellets for knowledge that peers trust (> 0.5 avg trust)
      if (avgTrust < 0.4) {
        log.engine.info(
          `[KnowledgeCouncil] Skipping pellet for "${learning.topic}" — low peer trust (${(avgTrust * 100).toFixed(0)}%)`,
        );
        continue;
      }

      const challenges = reviews.filter((r) => r.type === "challenge");
      const expansions = reviews.filter((r) => r.type === "expand");

      // Build enriched content from learning + peer feedback
      const enrichedContent = [
        `# ${learning.topic}`,
        ``,
        `*Researched by ${learning.owlEmoji} ${learning.owlName} | Peer-reviewed by ${reviews.length} owl(s) | Trust: ${(avgTrust * 100).toFixed(0)}%*`,
        ``,
        `## Findings`,
        learning.findings,
        ``,
        `## Key Insights`,
        ...learning.keyInsights.map((i) => `- ${i}`),
        ...(challenges.length > 0
          ? [
              ``,
              `## Challenges Raised`,
              ...challenges.map(
                (c) => `- **${c.reviewerName}**: ${c.feedback}`,
              ),
            ]
          : []),
        ...(expansions.length > 0
          ? [
              ``,
              `## Expansions`,
              ...expansions.map(
                (e) => `- **${e.reviewerName}**: ${e.feedback}`,
              ),
            ]
          : []),
        ...(learning.openQuestions.length > 0
          ? [
              ``,
              `## Open Questions`,
              ...learning.openQuestions.map((q) => `- ${q}`),
            ]
          : []),
      ].join("\n");

      try {
        const owl =
          owls.find((o) => o.persona.name === learning.owlName) ?? owls[0];
        const pellet = await this.pelletGenerator.generate(
          enrichedContent,
          `Knowledge Council: ${learning.topic}`,
          { provider: this.provider, owl, config: this.config },
        );
        await this.pelletStore.save(pellet);
        session.pelletsCreated++;

        log.engine.info(`[KnowledgeCouncil] Saved pellet: ${pellet.id}.md`);
      } catch (err) {
        log.engine.warn(
          `[KnowledgeCouncil] Failed to create pellet for "${learning.topic}": ${err instanceof Error ? err.message : err}`,
        );
      }
    }

    // Create pellets for cross-pollination insights
    for (const cp of session.crossPollinations) {
      try {
        const content = [
          `# Cross-Domain Insight`,
          ``,
          `*Discovered during Knowledge Council by connecting ${cp.owls.join(" and ")}'s research*`,
          ``,
          `## Connection`,
          cp.connection,
          ``,
          `## Emergent Insight`,
          cp.emergentInsight,
        ].join("\n");

        const pellet = await this.pelletGenerator.generate(
          content,
          `Cross-Pollination: ${cp.connection.slice(0, 60)}`,
          { provider: this.provider, owl: owls[0], config: this.config },
        );
        await this.pelletStore.save(pellet);
        session.pelletsCreated++;
      } catch {
        // Non-critical
      }
    }

    await onProgress?.(
      `  📝 Created **${session.pelletsCreated}** validated knowledge pellets`,
    );
  }

  // ─── Update Owl Inner Lives ──────────────────────────────────────

  private async updateOwlInnerLives(
    session: CouncilSession,
    owls: OwlInstance[],
  ): Promise<void> {
    for (const owl of owls) {
      try {
        const innerLife = new OwlInnerLife(
          this.provider,
          owl,
          this.workspacePath,
        );
        await innerLife.load();

        // Form opinions based on what was learned
        const learning = session.learnings.find(
          (l) => l.owlName === owl.persona.name,
        );
        if (learning) {
          await innerLife.formOpinion(learning.topic, learning.findings);
        }

        // Form opinions based on peer feedback received
        const feedbackReceived = session.reviews.filter(
          (r) => r.targetOwl === owl.persona.name,
        );
        for (const review of feedbackReceived) {
          if (review.type === "challenge") {
            // Being challenged might shift the owl's views
            const challengeContext = `${review.reviewerName} challenged my work on "${
              session.learnings.find((l) => l.owlName === owl.persona.name)
                ?.topic ?? "a topic"
            }": ${review.feedback}`;
            await innerLife.formOpinion(
              `peer feedback from ${review.reviewerName}`,
              challengeContext,
            );
          }
        }

        // Cross-pollination might spark new desires
        for (const cp of session.crossPollinations) {
          if (cp.owls.includes(owl.persona.name)) {
            // The owl's work was part of a cross-domain connection — spark curiosity
            const otherOwl = cp.owls.find((o) => o !== owl.persona.name);
            if (otherOwl) {
              await innerLife.formOpinion(
                `connection with ${otherOwl}'s research`,
                cp.emergentInsight,
              );
            }
          }
        }
      } catch (err) {
        log.engine.warn(
          `[KnowledgeCouncil] Inner life update for ${owl.persona.name} failed: ${err instanceof Error ? err.message : err}`,
        );
      }
    }
  }

  // ─── Session Summary ───────────────────────────────────────────

  private async generateSummary(session: CouncilSession): Promise<string> {
    const lines: string[] = [];

    lines.push(
      `**${session.learnings.length} owls** studied independently, then peer-reviewed each other.\n`,
    );

    for (const learning of session.learnings) {
      const reviews = session.reviews.filter(
        (r) => r.targetOwl === learning.owlName,
      );
      const challenges = reviews.filter((r) => r.type === "challenge").length;
      const avgTrust =
        reviews.length > 0
          ? reviews.reduce((sum, r) => sum + r.trustScore, 0) / reviews.length
          : learning.confidence;

      lines.push(
        `${learning.owlEmoji} **${learning.owlName}** → *${learning.topic}*` +
          ` (${(avgTrust * 100).toFixed(0)}% peer trust` +
          `${challenges > 0 ? `, ${challenges} challenge(s)` : ""})`,
      );
    }

    if (session.crossPollinations.length > 0) {
      lines.push(
        `\n**${session.crossPollinations.length} cross-domain connections** discovered:`,
      );
      for (const cp of session.crossPollinations) {
        lines.push(
          `  🔗 ${cp.owls.join(" ↔ ")}: ${cp.emergentInsight.slice(0, 100)}`,
        );
      }
    }

    lines.push(
      `\n**${session.pelletsCreated} knowledge pellets** created and saved.`,
    );

    return lines.join("\n");
  }

  // ─── History ────────────────────────────────────────────────────

  private async loadHistory(): Promise<void> {
    try {
      const raw = await readFile(this.historyPath, "utf-8");
      this.history = JSON.parse(raw);
    } catch {
      this.history = {
        sessions: [],
        studiedTopics: [],
        suggestedTopics: [],
      };
    }
  }

  private async saveToHistory(session: CouncilSession): Promise<void> {
    if (!this.history) await this.loadHistory();

    this.history!.sessions.push({
      id: session.id,
      date: session.startedAt,
      topics: session.learnings.map((l) => l.topic),
      pelletsCreated: session.pelletsCreated,
      participantCount: session.learnings.length,
    });

    // Track studied topics
    for (const l of session.learnings) {
      if (!this.history!.studiedTopics.includes(l.topic)) {
        this.history!.studiedTopics.push(l.topic);
      }
    }

    // Keep history bounded
    if (this.history!.sessions.length > 50) {
      this.history!.sessions = this.history!.sessions.slice(-50);
    }
    if (this.history!.studiedTopics.length > 100) {
      this.history!.studiedTopics = this.history!.studiedTopics.slice(-100);
    }

    this.history!.lastCouncil = session.startedAt;

    await mkdir(join(this.historyPath, ".."), { recursive: true });
    await writeFile(this.historyPath, JSON.stringify(this.history, null, 2));
  }

  /**
   * Check if a council session should run (used by heartbeat scheduler).
   * Councils run weekly by default, or can be triggered manually.
   */
  shouldConvene(): boolean {
    if (!this.history?.lastCouncil) return true;

    const lastCouncil = new Date(this.history.lastCouncil);
    const daysSince =
      (Date.now() - lastCouncil.getTime()) / (1000 * 60 * 60 * 24);
    const intervalDays = this.config.council?.intervalDays ?? 7;

    return daysSince >= intervalDays;
  }

  /**
   * Get a summary of council history for user display.
   */
  getHistorySummary(): string {
    if (!this.history || this.history.sessions.length === 0) {
      return "No Knowledge Council sessions have been held yet.";
    }

    const recent = this.history.sessions.slice(-3);
    const lines = [
      `**${this.history.sessions.length} council sessions** held so far.\n`,
    ];
    lines.push("Recent sessions:");
    for (const s of recent) {
      lines.push(
        `  - ${s.date.split("T")[0]}: ${s.topics.join(", ")} (${s.pelletsCreated} pellets)`,
      );
    }
    if (this.history.suggestedTopics.length > 0) {
      lines.push(
        `\nSuggested for next session: ${this.history.suggestedTopics.slice(0, 3).join(", ")}`,
      );
    }
    return lines.join("\n");
  }
}
