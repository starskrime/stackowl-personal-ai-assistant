/**
 * StackOwl — Web UI Server
 *
 * Express backend that serves the static web UI and provides REST APIs
 * for interacting with owls, pellets, and parliament sessions.
 */

import express from 'express';
import { join } from 'node:path';
import type { OwlRegistry } from '../owls/registry.js';
import type { PelletStore } from '../pellets/store.js';
import type { ModelProvider } from '../providers/base.js';
import type { SessionStore } from '../memory/store.js';
import type { ToolRegistry } from '../tools/registry.js';
import type { StackOwlConfig } from '../config/loader.js';
import { OwlEngine } from '../engine/runtime.js';
import { ParliamentOrchestrator } from '../parliament/orchestrator.js';

export class StackOwlServer {
    private app: express.Express;
    private port: number;
    private engine: OwlEngine;

    constructor(
        private config: StackOwlConfig,
        private provider: ModelProvider,
        private owlRegistry: OwlRegistry,
        private pelletStore: PelletStore,
        private sessionStore: SessionStore,
        private toolRegistry: ToolRegistry,
        private workspacePath: string,
        port = 3000
    ) {
        this.app = express();
        this.port = port;
        this.engine = new OwlEngine();

        this.setupMiddleware();
        this.setupRoutes();
    }

    private setupMiddleware() {
        this.app.use(express.json());

        // Serve static files from src/web
        // In production, it would be dist/web
        const webDir = join(process.cwd(), 'src', 'web');
        this.app.use(express.static(webDir));
    }

    private setupRoutes() {
        // --- Owls API ---
        this.app.get('/api/owls', (_req, res) => {
            const owls = this.owlRegistry.listOwls().map(o => ({
                name: o.persona.name,
                emoji: o.persona.emoji,
                type: o.persona.type,
                challengeLevel: o.dna.evolvedTraits.challengeLevel,
                specialties: o.persona.specialties,
            }));
            res.json(owls);
        });

        // --- Chat API ---
        this.app.post('/api/chat', async (req, res) => {
            const { message, owlName } = req.body;
            if (!message) {
                res.status(400).json({ error: 'Message is required' });
                return;
            }

            const owl = owlName ? this.owlRegistry.get(owlName) : this.owlRegistry.getDefault();
            if (!owl) {
                res.status(404).json({ error: 'Owl not found' });
                return;
            }

            try {
                const session = await this.sessionStore.getRecentOrCreate(`web_${owl.persona.name}`);

                const response = await this.engine.run(message, {
                    provider: this.provider,
                    owl,
                    sessionHistory: session.messages,
                    config: this.config,
                    toolRegistry: this.toolRegistry,
                    cwd: this.workspacePath,
                });

                // Update session
                session.messages.push({ role: 'user', content: message });
                session.messages.push({ role: 'assistant', content: response.content, name: owl.persona.name });
                await this.sessionStore.saveSession(session);

                res.json({
                    owl: owl.persona.name,
                    content: response.content,
                });
            } catch (error) {
                console.error('[WebAPI] Chat error:', error);
                res.status(500).json({ error: 'Failed to process message' });
            }
        });

        // --- Pellets API ---
        this.app.get('/api/pellets', async (req, res) => {
            const { q } = req.query;
            let pellets = [];

            if (q && typeof q === 'string') {
                pellets = await this.pelletStore.search(q);
            } else {
                pellets = await this.pelletStore.listAll();
            }

            res.json(pellets);
        });

        // --- Parliament API ---
        this.app.post('/api/parliament', async (req, res) => {
            const { topic, owlNames } = req.body;
            if (!topic) {
                res.status(400).json({ error: 'Topic is required' });
                return;
            }

            const orchestrator = new ParliamentOrchestrator(this.provider, this.config, this.pelletStore);

            let participants = owlNames
                ? owlNames.map((n: string) => this.owlRegistry.get(n)).filter(Boolean)
                : this.owlRegistry.listOwls().slice(0, 3);

            if (participants.length < 2) {
                res.status(400).json({ error: 'At least 2 valid owls required' });
                return;
            }

            try {
                // To avoid request timeout on long debates, we might want to return a Job ID
                // But for MVP, we just await it (frontend needs a long timeout)
                const session = await orchestrator.convene({
                    topic,
                    participants,
                    contextMessages: []
                });

                const mdReport = orchestrator.formatSessionMarkdown(session);
                res.json({ report: mdReport });
            } catch (error) {
                res.status(500).json({ error: 'Parliament session failed' });
            }
        });
    }

    start(): Promise<void> {
        return new Promise((resolve) => {
            this.app.listen(this.port, () => {
                console.log(`\n🌐 StackOwl Web UI running at http://localhost:${this.port}`);
                resolve();
            });
        });
    }
}
