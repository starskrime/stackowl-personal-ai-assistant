/**
 * StackOwl — Parliament Orchestrator
 *
 * Runs multi-owl brainstorming sessions.
 */

import { v4 as uuidv4 } from 'uuid';
import type { ModelProvider } from '../providers/base.js';
import type { StackOwlConfig } from '../config/loader.js';
import { OwlEngine } from '../engine/runtime.js';
import type { ParliamentConfig, ParliamentSession, OwlPosition } from './protocol.js';
import { PelletGenerator } from '../pellets/generator.js';
import type { PelletStore } from '../pellets/store.js';

export class ParliamentOrchestrator {
    private provider: ModelProvider;
    private engine: OwlEngine;
    private config: StackOwlConfig;
    private pelletGenerator: PelletGenerator;
    private pelletStore: PelletStore;

    constructor(provider: ModelProvider, config: StackOwlConfig, pelletStore: PelletStore) {
        this.provider = provider;
        this.config = config;
        this.pelletStore = pelletStore;
        this.engine = new OwlEngine();
        this.pelletGenerator = new PelletGenerator();
    }

    /**
     * Start and run a full Parliament session.
     */
    async convene(config: ParliamentConfig): Promise<ParliamentSession> {
        const session: ParliamentSession = {
            id: uuidv4(),
            config,
            phase: 'setup',
            positions: [],
            challenges: [],
            startedAt: Date.now(),
        };

        if (config.participants.length < 2) {
            throw new Error('A Parliament requires at least 2 owls.');
        }

        console.log(`\n🏛️  PARLIAMENT CONVENED: ${config.topic}`);
        console.log(`👥 Participants: ${config.participants.map(o => `${o.persona.emoji} ${o.persona.name}`).join(', ')}\n`);

        try {
            await this.runRound1(session);
            await this.runRound2(session);
            await this.runRound3(session);

            session.completedAt = Date.now();
            session.phase = 'complete';

            // Automatically generate a Pellet from this session
            const mdTranscript = this.formatSessionMarkdown(session);
            try {
                const pellet = await this.pelletGenerator.generate(
                    mdTranscript,
                    `Parliament Session: ${config.topic}`,
                    { provider: this.provider, owl: config.participants[0], config: this.config }
                );
                await this.pelletStore.save(pellet);
                console.log(`\n📦 Saved Knowledge Pellet: ${pellet.id}.md`);
            } catch (pelletError) {
                console.error('[Parliament] Failed to generate pellet:', pelletError);
            }

            return session;
        } catch (error) {
            console.error('[Parliament] Session failed:', error);
            throw error;
        }
    }

    /**
     * Round 1: Initial Positions (Parallel)
     */
    private async runRound1(session: ParliamentSession): Promise<void> {
        session.phase = 'round1_position';
        console.log(`\n--- ROUND 1: INITIAL POSITIONS ---`);

        const promises = session.config.participants.map(async (owl) => {
            const prompt = `PARLIAMENT TOPIC: ${session.config.topic}\n\n` +
                `Task: Provide your initial hardline position on this topic based on your sole expertise (${owl.persona.type}). ` +
                `State exactly one of these positions at the very beginning of your response: [FOR, AGAINST, CONDITIONAL, NEUTRAL, ANALYSIS]. ` +
                `Then provide a single paragraph (max 4 sentences) arguing your case. Be opinionated.`;

            // Pass actual session context so owls know what the user was discussing
            const sessionHistory = session.config.contextMessages.map(m => ({
                role: m.role as import('../providers/base.js').MessageRole,
                content: m.content,
            }));

            const response = await this.engine.run(prompt, {
                provider: this.provider,
                owl,
                sessionHistory,
                config: this.config,
            });

            // Extract position tag
            let positionScore: OwlPosition['position'] = 'ANALYSIS';
            const tags = ['FOR', 'AGAINST', 'CONDITIONAL', 'NEUTRAL', 'ANALYSIS'] as const;
            for (const tag of tags) {
                if (response.content.toUpperCase().includes(`[${tag}]`) || response.content.startsWith(tag)) {
                    positionScore = tag;
                    break;
                }
            }

            // Clean content
            let cleanArg = response.content;
            for (const tag of tags) {
                cleanArg = cleanArg.replace(`[${tag}]`, '').replace(new RegExp(`^${tag}[:\\s]*`, 'i'), '').trim();
            }

            const position: OwlPosition = {
                owlName: owl.persona.name,
                owlEmoji: owl.persona.emoji,
                position: positionScore,
                argument: cleanArg,
            };

            session.positions.push(position);
            console.log(`${owl.persona.emoji} ${owl.persona.name} [${positionScore}]: ${cleanArg}`);

            return position;
        });

        await Promise.all(promises);
    }

    /**
     * Round 2: Cross-Examination (Sequential)
     */
    private async runRound2(session: ParliamentSession): Promise<void> {
        session.phase = 'round2_challenge';
        console.log(`\n--- ROUND 2: CROSS-EXAMINATION ---`);

        const allPositions = session.positions.map(p =>
            `- ${p.owlName} [${p.position}]: ${p.argument}`
        ).join('\n\n');

        // Pick the single most contrary/challenging owl to lead cross-examination.
        // Running all non-low owls produces redundant, conflicting critiques.
        // Priority: relentless > high > medium — then fall back to first participant.
        const challengeRank: Record<string, number> = { relentless: 3, high: 2, medium: 1, low: 0 };
        const challenger = session.config.participants
            .filter(o => o.dna.evolvedTraits.challengeLevel !== 'low')
            .sort((a, b) => (challengeRank[b.dna.evolvedTraits.challengeLevel] ?? 0) - (challengeRank[a.dna.evolvedTraits.challengeLevel] ?? 0))[0]
            ?? session.config.participants[0];

        for (const owl of [challenger]) {
            // Only the chosen challenger runs

            const prompt = `PARLIAMENT TOPIC: ${session.config.topic}\n\n` +
                `Other owls have stated their positions:\n${allPositions}\n\n` +
                `Task: Review the positions. If you see a gaping hole in someone's logic, a missed risk, or a naive assumption, ` +
                `call them out specifically. Name the owl you are challenging. Keep it to 2-3 sentences. ` +
                `If everyone is mostly right, play devil's advocate.`;

            const sessionHistory = session.config.contextMessages.map(m => ({
                role: m.role as import('../providers/base.js').MessageRole,
                content: m.content,
            }));

            const response = await this.engine.run(prompt, {
                provider: this.provider,
                owl,
                sessionHistory,
                config: this.config,
            });

            // Try to figure out who they challenged
            let targetOwl = '';
            for (const p of session.config.participants) {
                if (p.persona.name !== owl.persona.name && response.content.includes(p.persona.name)) {
                    targetOwl = p.persona.name;
                    break;
                }
            }
            if (!targetOwl) targetOwl = 'Group';

            session.challenges.push({
                owlName: owl.persona.name,
                targetOwl,
                challengeContent: response.content,
            });

            console.log(`${owl.persona.emoji} ${owl.persona.name} (challenging ${targetOwl}): ${response.content}`);
        }
    }

    /**
     * Round 3: Synthesis
     */
    private async runRound3(session: ParliamentSession): Promise<void> {
        session.phase = 'round3_synthesis';
        console.log(`\n--- ROUND 3: SYNTHESIS ---`);

        // Usually Noctua (the Executive Assistant) or Athena (the Architect) performs synthesis
        let synthesizer = session.config.participants.find(o => o.persona.name === 'Noctua');
        if (!synthesizer) synthesizer = session.config.participants.find(o => o.persona.type === 'architect');
        if (!synthesizer) synthesizer = session.config.participants[0];

        const history = `TOPIC: ${session.config.topic}\n\n` +
            `Positions:\n${session.positions.map(p => `- ${p.owlName} [${p.position}]: ${p.argument}`).join('\n')}\n\n` +
            `Challenges:\n${session.challenges.map(c => `- ${c.owlName} challenged ${c.targetOwl}: ${c.challengeContent}`).join('\n')}`;

        const prompt = `Here is the transcript of a Parliament session:\n\n${history}\n\n` +
            `Task: Synthesize this debate into a final verdict. ` +
            `1. Provide a clear recommendation (e.g., PROCEED, HOLD, ABORT, REVISE). ` +
            `2. Summarize the critical tradeoffs identified by the group. ` +
            `3. Suggest the concrete next step. ` +
            `Do NOT give a non-answer. Make a call even if the group is divided.`;

        const sessionHistory = session.config.contextMessages.map(m => ({
            role: m.role as import('../providers/base.js').MessageRole,
            content: m.content,
        }));

        const response = await this.engine.run(prompt, {
            provider: this.provider,
            owl: synthesizer,
            sessionHistory,
            config: this.config,
        });

        session.synthesis = response.content;

        // Try to extract a one word verdict
        const match = response.content.match(/\b(PROCEED|HOLD|ABORT|REVISE|APPROVE|REJECT)\b/i);
        session.verdict = match ? match[1].toUpperCase() : 'CONSENSUS_REACHED';

        console.log(`${synthesizer.persona.emoji} ${synthesizer.persona.name} [SYNTHESIS - ${session.verdict}]:\n${response.content}`);
    }

    /**
     * Format a session into a readable string markdown
     */
    formatSessionMarkdown(session: ParliamentSession): string {
        let md = `🏛️ **PARLIAMENT SESSION:** ${session.config.topic}\n`;
        md += `═══════════════════════════════════════════════════════\n\n`;

        for (const p of session.positions) {
            md += `${p.owlEmoji} **${p.owlName}**: [${p.position}] — "${p.argument}"\n\n`;
        }

        if (session.challenges.length > 0) {
            md += `*Cross-Examination:*\n`;
            for (const c of session.challenges) {
                const owl = session.config.participants.find(o => o.persona.name === c.owlName);
                md += `> ${owl?.persona.emoji} **${c.owlName}** (to ${c.targetOwl}): "${c.challengeContent}"\n`;
            }
            md += `\n`;
        }

        md += `📋 **PARLIAMENT VERDICT**: [${session.verdict || 'PENDING'}]\n`;
        md += `${session.synthesis}\n`;

        return md;
    }
}
