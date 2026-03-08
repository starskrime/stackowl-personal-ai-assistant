/**
 * StackOwl — Main Entry Point
 *
 * Initializes the StackOwl system and starts the CLI interface.
 */

import { resolve } from 'node:path';
import { program } from 'commander';
import chalk from 'chalk';
import { loadConfig } from './config/loader.js';
import { ProviderRegistry } from './providers/registry.js';
import { OwlRegistry } from './owls/registry.js';
import { OwlEngine } from './engine/runtime.js';
import { TelegramChannel } from './channels/telegram.js';
import { ToolRegistry } from './tools/registry.js';
import { ShellTool } from './tools/shell.js';
import { ReadFileTool, WriteFileTool } from './tools/files.js';
import { WebFetchTool } from './tools/web.js';
import { SessionStore } from './memory/store.js';
import { ParliamentOrchestrator } from './parliament/orchestrator.js';
import { PelletStore } from './pellets/store.js';
import { OwlEvolutionEngine } from './owls/evolution.js';
import { ToolSynthesizer } from './evolution/synthesizer.js';
import { CapabilityLedger } from './evolution/ledger.js';
import { DynamicToolLoader } from './evolution/loader.js';
import { EvolutionHandler } from './evolution/handler.js';
import { InstinctRegistry } from './instincts/registry.js';
import { InstinctEngine } from './instincts/engine.js';
import { PerchManager } from './perch/manager.js';
import { FilePerch } from './perch/file-perch.js';
import { StackOwlServer } from './server/index.js';
import { createInterface } from 'node:readline';
import { readFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { join } from 'node:path';

// ─── ASCII Art Banner ────────────────────────────────────────────

const BANNER = `
${chalk.yellow('   _____ __             __   ____            __')}
${chalk.yellow('  / ___// /_____ ______/ /__/ __ \\\\__      __/ /')}
${chalk.yellow('  \\\\__ \\\\/ __/ __ `/ ___/ //_/ / / / | /| / / / ')}
${chalk.yellow(' ___/ / /_/ /_/ / /__/ ,< / /_/ /| |/ |/ / /  ')}
${chalk.yellow('/____/\\\\__/\\\\__,_/\\\\___/_/|_|\\\\____/ |__/|__/_/   ')}
${chalk.dim('──────────────────────────────────────────────────')}
${chalk.dim('🦉 Personal AI Assistant • Challenge Everything')}
${chalk.dim('──────────────────────────────────────────────────')}
`;

// ─── Bootstrap StackOwl ──────────────────────────────────────────

async function bootstrap() {
    const basePath = process.cwd();
    const config = await loadConfig(basePath);
    const workspacePath = resolve(basePath, config.workspace);

    // Initialize provider registry
    const providerRegistry = new ProviderRegistry();
    for (const [name, providerConf] of Object.entries(config.providers)) {
        providerRegistry.register({
            name,
            ...providerConf,
        });
    }
    providerRegistry.setDefault(config.defaultProvider);

    // Initialize owl registry
    const owlRegistry = new OwlRegistry(workspacePath);
    await owlRegistry.loadAll();

    // Initialize engine
    const engine = new OwlEngine();

    // Initialize tools
    const toolRegistry = new ToolRegistry();
    toolRegistry.registerAll([
        ShellTool,
        ReadFileTool,
        WriteFileTool,
        WebFetchTool,
    ]);

    // Initialize session store
    const sessionStore = new SessionStore(workspacePath);
    await sessionStore.init();

    // Initialize pellet store
    const pelletStore = new PelletStore(workspacePath, providerRegistry.getDefault());
    await pelletStore.init();

    // Evolution Engine (DNA)
    const evolutionEngine = new OwlEvolutionEngine(providerRegistry.getDefault(), config, sessionStore, owlRegistry);

    // Self-improvement system
    const synthesizer = new ToolSynthesizer();
    const ledger = new CapabilityLedger();
    const loader = new DynamicToolLoader(ledger);
    const evolution = new EvolutionHandler(synthesizer, ledger, loader);

    // Load any previously synthesized tools into the registry
    const synthesizedCount = await loader.loadAll(toolRegistry);
    if (synthesizedCount > 0) {
        console.log(chalk.dim(`  [Loaded ${synthesizedCount} synthesized tool(s) from previous sessions]`));
    }

    // Instincts
    const instinctRegistry = new InstinctRegistry(workspacePath);
    await instinctRegistry.loadAll();
    const instinctEngine = new InstinctEngine();

    // Perch Points
    const perchManager = new PerchManager(providerRegistry.getDefault(), config, owlRegistry);
    perchManager.addPerch(new FilePerch(workspacePath));

    return {
        config,
        providerRegistry,
        owlRegistry,
        engine,
        toolRegistry,
        sessionStore,
        pelletStore,
        evolutionEngine,
        instinctRegistry,
        instinctEngine,
        perchManager,
        workspacePath,
        evolution,
        synthesizer,
        ledger,
        loader,
    };
}

// ─── Chat Command ────────────────────────────────────────────────

async function chatCommand(owlName?: string) {
    console.log(BANNER);

    const { providerRegistry, owlRegistry, engine, config, toolRegistry, sessionStore, instinctRegistry, instinctEngine, perchManager, workspacePath, evolution } = await bootstrap();

    // Select owl
    const owl = owlName
        ? owlRegistry.get(owlName)
        : owlRegistry.getDefault();

    if (!owl) {
        console.error(chalk.red(`❌ Owl "${owlName}" not found.`));
        console.log(chalk.dim('Available owls:'));
        for (const o of owlRegistry.listOwls()) {
            console.log(chalk.dim(`  ${o.persona.emoji} ${o.persona.name} (${o.persona.type})`));
        }
        process.exit(1);
    }

    const provider = providerRegistry.getDefault();

    // Health check
    const healthy = await provider.healthCheck();
    if (!healthy) {
        console.error(
            chalk.red(`❌ Cannot reach ${provider.name} provider. Is it running?`)
        );
        process.exit(1);
    }

    console.log(
        chalk.green(`✓ Connected to ${provider.name}`) +
        chalk.dim(` (model: ${config.defaultModel})`)
    );
    console.log(
        chalk.green(`✓ Active owl: ${owl.persona.emoji} ${owl.persona.name}`) +
        chalk.dim(` (${owl.persona.type}, challenge: ${owl.dna.evolvedTraits.challengeLevel})`)
    );
    console.log(
        chalk.dim(`\nType your message. Commands: ${chalk.bold('/quit')} · ${chalk.bold('/owls')} · ${chalk.bold('/status')} · ${chalk.bold('/capabilities')}\n`)
    );

    const session = await sessionStore.getRecentOrCreate(owl.persona.name);
    const sessionHistory = session.messages;

    if (sessionHistory.length > 0) {
        console.log(chalk.dim(`  [Resumed session with ${sessionHistory.length / 2} past turns]\n`));
    }

    // Start perches (passive observation)
    await perchManager.startAll();

    const rl = createInterface({
        input: process.stdin,
        output: process.stdout,
        prompt: chalk.cyan('You: '),
    });

    rl.prompt();

    rl.on('line', async (line) => {
        const input = line.trim();
        if (!input) {
            rl.prompt();
            return;
        }

        // Handle commands
        if (input === '/quit' || input === '/exit') {
            console.log(chalk.dim('\n🦉 Goodbye. The owls are always watching.\n'));
            perchManager.stopAll();
            rl.close();
            process.exit(0);
        }

        if (input === '/owls') {
            console.log(chalk.bold('\nAvailable Owls:'));
            for (const o of owlRegistry.listOwls()) {
                console.log(`  ${o.persona.emoji} ${chalk.bold(o.persona.name)} — ${o.persona.type} (challenge: ${o.dna.evolvedTraits.challengeLevel})`);
            }
            console.log('');
            rl.prompt();
            return;
        }

        if (input === '/status') {
            console.log(chalk.bold('\nStatus:'));
            console.log(`  Provider: ${provider.name}`);
            console.log(`  Model: ${config.defaultModel}`);
            console.log(`  Owl: ${owl.persona.emoji} ${owl.persona.name}`);
            console.log(`  DNA Generation: ${owl.dna.generation}`);
            console.log(`  Session messages: ${sessionHistory.length}`);
            console.log('');
            rl.prompt();
            return;
        }

        if (input === '/capabilities') {
            const records = await evolution.listAll();
            if (records.length === 0) {
                console.log(chalk.dim('\n  No synthesized tools yet. The owl will build them when needed.\n'));
            } else {
                console.log(chalk.bold('\n🔧 Synthesized Tools:\n'));
                for (const r of records) {
                    const icon = r.status === 'active' ? chalk.green('✓') : r.status === 'failed' ? chalk.red('✗') : chalk.dim('⊘');
                    console.log(`  ${icon} ${chalk.bold(r.toolName)}`);
                    console.log(`     ${chalk.dim(r.description)}`);
                    console.log(`     ${chalk.dim(`By: ${r.createdBy} | Used: ${r.timesUsed}x | Status: ${r.status}`)}`);
                    if (r.dependencies.length > 0) {
                        console.log(`     ${chalk.dim(`Deps: ${r.dependencies.join(', ')}`)}`);
                    }
                    console.log('');
                }
            }
            rl.prompt();
            return;
        }

        // Send to engine
        try {
            // 1. Check Instincts
            const availableInstincts = instinctRegistry.getContextInstincts(owl.persona.name);
            const triggeredInstinct = await instinctEngine.evaluate(input, availableInstincts, {
                provider,
                owl,
                config
            });

            let finalProcessingInput = input;
            if (triggeredInstinct) {
                console.log(chalk.yellow(`\n⚡ Instinct Triggered: ${triggeredInstinct.name.toUpperCase()}`));
                finalProcessingInput = `User Input: ${input}\n\n[SYSTEM OVERRIDE - INSTINCT TRIGGERED]\n${triggeredInstinct.actionPrompt}`;
            }

            process.stdout.write(chalk.yellow(`\n${owl.persona.emoji} ${owl.persona.name}: `));

            const response = await engine.run(finalProcessingInput, {
                provider,
                owl,
                sessionHistory,
                config,
                toolRegistry,
                cwd: workspacePath,
            });

            // ─── Self-Improvement: Capability Gap Detected ──────────────
            if (response.pendingCapabilityGap) {
                console.log(response.content);
                console.log('');

                console.log(chalk.dim(`\n🧠 Reasoning about what tool would help...`));
                const proposal = await evolution.designSpec(response.pendingCapabilityGap, {
                    provider, owl, sessionHistory, config, toolRegistry, cwd: workspacePath,
                });

                console.log(chalk.bold.cyan(`\n⚡ Capability Gap — Tool Proposal`));
                console.log(chalk.dim('─'.repeat(52)));
                console.log(`  ${chalk.bold('Tool name:')}    ${proposal.toolName}`);
                console.log(`  ${chalk.bold('What it does:')} ${proposal.description}`);
                if (proposal.parameters.length > 0) {
                    console.log(`  ${chalk.bold('Parameters:')}`);
                    for (const p of proposal.parameters) {
                        console.log(`    • ${p.name} (${p.type})${p.required ? '' : ' (optional)'} — ${p.description}`);
                    }
                }
                if (proposal.dependencies.length > 0) {
                    console.log(`  ${chalk.bold('npm deps:')}     ${proposal.dependencies.join(', ')}`);
                }
                console.log(`  ${chalk.bold('Safety:')}       ${proposal.safetyNote}`);
                console.log(`  ${chalk.bold('Why:')}          ${proposal.rationale}`);
                console.log(`  ${chalk.bold('File:')}         src/tools/synthesized/${proposal.toolName}.ts`);
                console.log(chalk.dim('─'.repeat(52)));

                const answer = await new Promise<string>((resolve) => {
                    rl.question(chalk.cyan('\nBuild this tool? [y/n]: '), resolve);
                });

                if (answer.trim().toLowerCase().startsWith('y')) {
                    console.log(chalk.dim(`\n🔧 Generating implementation...`));
                    try {
                        const engineContext = { provider, owl, sessionHistory, config, toolRegistry, cwd: workspacePath };

                        const askInstall = async (deps: string[]) => {
                            const installAnswer = await new Promise<string>((resolve) => {
                                rl.question(
                                    chalk.cyan(`\n📦 Install npm deps (${deps.join(', ')})? [y/n]: `),
                                    resolve
                                );
                            });
                            return installAnswer.trim().toLowerCase().startsWith('y');
                        };

                        const onProgress = async (msg: string) => {
                            console.log(chalk.dim(`  ${msg}`));
                        };

                        const { response: retryResponse, depsToInstall, depsInstalled } = await evolution.buildAndRetry(
                            proposal, finalProcessingInput, engineContext, engine, askInstall, onProgress
                        );

                        console.log(chalk.green(`\n✓ Tool "${proposal.toolName}" built and loaded.`));
                        if (depsInstalled) {
                            console.log(chalk.green(`  ✓ npm deps installed.`));
                        } else if (depsToInstall.length > 0) {
                            console.log(chalk.yellow(`  ⚠ Deps not installed — run manually: npm install ${depsToInstall.join(' ')}`));
                        }
                        console.log(chalk.dim(`\n🔄 Retrying...\n`));
                        process.stdout.write(chalk.yellow(`${owl.persona.emoji} ${owl.persona.name}: `));
                        console.log(retryResponse.content);

                        sessionHistory.push({ role: 'user', content: input });
                        sessionHistory.push({ role: 'assistant', content: retryResponse.content });
                        await sessionStore.saveSession(session);

                        if (retryResponse.usage) {
                            console.log(chalk.dim(`  [tokens: ${retryResponse.usage.promptTokens}→${retryResponse.usage.completionTokens}]`));
                        }
                    } catch (err) {
                        const msg = err instanceof Error ? err.message : String(err);
                        console.error(chalk.red(`\n❌ Synthesis failed: ${msg}\n`));
                        sessionHistory.push({ role: 'user', content: input });
                        sessionHistory.push({ role: 'assistant', content: response.content });
                        await sessionStore.saveSession(session);
                    }
                } else {
                    console.log(chalk.dim('\n↩ Skipped.\n'));
                    sessionHistory.push({ role: 'user', content: input });
                    sessionHistory.push({ role: 'assistant', content: response.content });
                    await sessionStore.saveSession(session);
                }

                console.log('');
                rl.prompt();
                return;
            }
            // ─────────────────────────────────────────────────────────────

            console.log(response.content);

            // Update session history
            sessionHistory.push({ role: 'user', content: input });
            sessionHistory.push({ role: 'assistant', content: response.content });

            // Save to disk
            await sessionStore.saveSession(session);

            if (response.usage) {
                console.log(
                    chalk.dim(`  [tokens: ${response.usage.promptTokens}→${response.usage.completionTokens}]`)
                );
            }
            console.log('');
        } catch (error) {
            const msg = error instanceof Error ? error.message : String(error);
            console.error(chalk.red(`\n❌ Error: ${msg}\n`));
        }

        rl.prompt();
    });
}

// ─── Parliament Command ──────────────────────────────────────────

async function parliamentCommand(topic?: string) {
    console.log(BANNER);

    if (!topic || topic.trim() === '') {
        console.error(chalk.red('❌ Please provide a topic for the Parliament to debate.'));
        console.log(chalk.dim('Example: stackowl parliament "Should we migrate from PostgreSQL to DynamoDB?"'));
        process.exit(1);
    }

    const { providerRegistry, owlRegistry, config, pelletStore } = await bootstrap();
    const provider = providerRegistry.getDefault();

    // Pick 3-4 owls for the debate (default to Noctua, Archimedes, Scrooge, and Socrates if available)
    const participants = [
        owlRegistry.get('Noctua'),
        owlRegistry.get('Archimedes'),
        owlRegistry.get('Scrooge'),
        owlRegistry.get('Socrates'),
    ].filter(Boolean) as any[];

    if (participants.length < 2) {
        // Fallback to whatever owls we have
        const allOwls = owlRegistry.listOwls();
        if (allOwls.length < 2) {
            console.error(chalk.red('❌ Parliament requires at least 2 owls. Create more OWL.md files.'));
            process.exit(1);
        }
        participants.length = 0;
        participants.push(...allOwls.slice(0, 4));
    }

    console.log(chalk.cyan(`\nSummoning Parliament...\n`));

    const orchestrator = new ParliamentOrchestrator(provider, config, pelletStore);

    try {
        const session = await orchestrator.convene({
            topic,
            participants,
            contextMessages: [],
        });

        console.log('\n\n' + chalk.bold.green('=== FINAL REPORT ===\n'));
        console.log(orchestrator.formatSessionMarkdown(session));
    } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        console.error(chalk.red(`\nParliament session failed: ${msg}`));
    }
}

// ─── Owls Command ────────────────────────────────────────────────

async function owlsCommand() {
    const { owlRegistry } = await bootstrap();
    const owls = owlRegistry.listOwls();

    console.log(chalk.bold('\n🦉 StackOwl — Registered Owls\n'));

    if (owls.length === 0) {
        console.log(chalk.dim('  No owls found. Check your workspace/owls/ directory.'));
        return;
    }

    for (const owl of owls) {
        const p = owl.persona;
        const d = owl.dna;
        console.log(`  ${p.emoji} ${chalk.bold(p.name)} — ${p.type}`);
        console.log(chalk.dim(`     Challenge: ${d.evolvedTraits.challengeLevel} | Gen: ${d.generation} | Convos: ${d.interactionStats.totalConversations}`));
        console.log(chalk.dim(`     Specialties: ${p.specialties.join(', ')}`));
        console.log('');
    }
}

// ─── Status Command ──────────────────────────────────────────────

async function statusCommand() {
    const { config, providerRegistry } = await bootstrap();

    console.log(chalk.bold('\n🦉 StackOwl — System Status\n'));

    const healthResults = await providerRegistry.healthCheckAll();
    for (const [name, healthy] of Object.entries(healthResults)) {
        const icon = healthy ? chalk.green('✓') : chalk.red('✗');
        const label = name === config.defaultProvider ? `${name} (default)` : name;
        console.log(`  ${icon} ${label}`);
    }

    console.log(`\n  Default model: ${config.defaultModel}`);
    console.log(`  Gateway: ws://${config.gateway.host}:${config.gateway.port}`);
    console.log(`  Workspace: ${config.workspace}`);
    console.log('');
}

// ─── Pellets Command ───────────────────────────────────────────────

async function pelletsCommand(opts: { search?: string; read?: string }) {
    console.log(BANNER);

    const { pelletStore } = await bootstrap();

    if (opts.read) {
        // Read a specific pellet
        const pellet = await pelletStore.get(opts.read);
        if (!pellet) {
            console.error(chalk.red(`❌ Pellet "${opts.read}" not found.`));
            process.exit(1);
        }

        console.log(chalk.bold.cyan(`📦 PELLET: ${pellet.title}`));
        console.log(chalk.dim(`Generated: ${new Date(pellet.generatedAt).toLocaleString()}`));
        console.log(chalk.dim(`Source: ${pellet.source}`));
        console.log(chalk.dim(`Tags: ${pellet.tags.join(', ')}`));
        console.log(chalk.dim(`Owls: ${pellet.owls.join(', ')}`));
        console.log('\n' + pellet.content);
        return;
    }

    // List or search pellets
    let pellets = await pelletStore.listAll();

    if (opts.search) {
        pellets = await pelletStore.search(opts.search);
        console.log(chalk.cyan(`🔍 Search results for "${opts.search}":\n`));
    } else {
        console.log(chalk.cyan(`📦 Knowledge Pellets:\n`));
    }

    if (pellets.length === 0) {
        console.log(chalk.dim('No pellets found. Trigger a Parliament session to generate some.'));
        return;
    }

    for (const p of pellets) {
        console.log(`${chalk.bold(p.title)} ${chalk.dim(`(ID: ${p.id})`)}`);
        console.log(`  ${chalk.dim('Tags: ')} ${p.tags.join(', ')}`);
        console.log(`  ${chalk.dim('Owls: ')} ${p.owls.join(', ')}`);
        console.log('');
    }
}

// ─── Evolve Command ────────────────────────────────────────────────

async function evolveCommand(owlName: string) {
    console.log(BANNER);

    const { evolutionEngine } = await bootstrap();

    if (!owlName) {
        console.error(chalk.red('❌ Please provide an owl name to evolve.'));
        process.exit(1);
    }

    try {
        const mutated = await evolutionEngine.evolve(owlName);
        if (!mutated) {
            console.log(chalk.yellow(`\n🦤 No evolution triggered for ${owlName}. They didn't learn anything new.`));
        }
    } catch (error) {
        console.error(chalk.red('\nEvolution failed:'), error);
        process.exit(1);
    }
}

// ─── Telegram Command ────────────────────────────────────────────

async function telegramCommand(opts: { owl?: string; withCli?: boolean }) {
    console.log(BANNER);

    const { providerRegistry, owlRegistry, config, toolRegistry, sessionStore, workspacePath, evolution } = await bootstrap();

    // Get Telegram bot token from config or credentials file
    let botToken = '';
    const configAny = config as unknown as Record<string, unknown>;
    const telegramConfig = configAny['telegram'] as { botToken?: string; enabled?: boolean } | undefined;

    if (telegramConfig?.botToken) {
        botToken = telegramConfig.botToken;
    } else {
        // Try credentials file
        const credPath = join(process.cwd(), '.stackowl.credentials.json');
        if (existsSync(credPath)) {
            try {
                const creds = JSON.parse(await readFile(credPath, 'utf-8')) as Record<string, string>;
                botToken = creds['telegramBotToken'] ?? '';
            } catch {
                // Ignore parse errors
            }
        }
    }

    if (!botToken) {
        console.error(chalk.red('❌ Telegram bot token not found.'));
        console.log(chalk.dim('  Run ./start.sh to configure, or add "telegram.botToken" to stackowl.config.json'));
        process.exit(1);
    }

    const owl = opts.owl
        ? owlRegistry.get(opts.owl)
        : owlRegistry.getDefault();

    if (!owl) {
        console.error(chalk.red(`❌ Owl "${opts.owl}" not found.`));
        process.exit(1);
    }

    const provider = providerRegistry.getDefault();

    // Health check
    const healthy = await provider.healthCheck();
    if (!healthy) {
        console.error(
            chalk.red(`❌ Cannot reach ${provider.name} provider. Is it running?`)
        );
        process.exit(1);
    }

    console.log(chalk.green(`✓ Provider: ${provider.name}`) + chalk.dim(` (model: ${config.defaultModel})`));
    console.log(chalk.green(`✓ Owl: ${owl.persona.emoji} ${owl.persona.name}`));
    console.log(chalk.green(`✓ Channel: 📱 Telegram`));

    // Create telegram channel first so perch can use it
    const telegram = new TelegramChannel({
        botToken,
        provider,
        owl,
        config,
        toolRegistry,
        sessionStore,
        cwd: workspacePath,
        evolution,
    });

    // We must hackilly inject telegram into perchManager for now, or just recreate it
    // To match our MVP architecture, we can just attach it loosely or recreate it
    const perchWithTelegram = new PerchManager(provider, config, owlRegistry, telegram);
    perchWithTelegram.addPerch(new FilePerch(workspacePath));
    await perchWithTelegram.startAll();

    // Graceful shutdown
    const shutdown = () => {
        console.log(chalk.dim('\n🦉 Shutting down Telegram bot & observers...'));
        perchWithTelegram.stopAll();
        telegram.stop();
        process.exit(0);
    };
    process.on('SIGINT', shutdown);
    process.on('SIGTERM', shutdown);

    // Start Telegram bot
    await telegram.start();

    // Optionally also launch CLI chat
    if (opts.withCli) {
        console.log(chalk.dim('\n📱 Telegram bot running in background. CLI also active.\n'));
        await chatCommand(opts.owl);
    }
}

// ─── Web Command ─────────────────────────────────────────────────

async function webCommand(port?: string) {
    console.log(BANNER);
    const resolvedPort = port ? parseInt(port, 10) : 3000;

    const { config, providerRegistry, owlRegistry, pelletStore, sessionStore, toolRegistry, workspacePath } = await bootstrap();
    const provider = providerRegistry.getDefault();

    // Health check
    const healthy = await provider.healthCheck();
    if (!healthy) {
        console.error(chalk.red(`❌ Cannot reach ${provider.name} provider. Is it running?`));
        process.exit(1);
    }

    const server = new StackOwlServer(
        config,
        provider,
        owlRegistry,
        pelletStore,
        sessionStore,
        toolRegistry,
        workspacePath,
        resolvedPort
    );

    await server.start();
}

// ─── All Command ─────────────────────────────────────────────────

async function allCommand(opts: { owl?: string; port?: string }) {
    // 1. Start Web Server
    const resolvedPort = opts.port ? parseInt(opts.port, 10) : 3000;
    const { config, providerRegistry, owlRegistry, pelletStore, sessionStore, toolRegistry, workspacePath } = await bootstrap();
    const provider = providerRegistry.getDefault();

    const healthy = await provider.healthCheck();
    if (!healthy) {
        console.error(chalk.red(`❌ Cannot reach ${provider.name} provider. Is it running?`));
        process.exit(1);
    }

    const server = new StackOwlServer(config, provider, owlRegistry, pelletStore, sessionStore, toolRegistry, workspacePath, resolvedPort);
    await server.start();

    // 2. Check for Telegram
    let botToken = '';
    const configAny = config as unknown as Record<string, unknown>;
    const telegramConfig = configAny['telegram'] as { botToken?: string; enabled?: boolean } | undefined;

    if (telegramConfig?.botToken) {
        botToken = telegramConfig.botToken;
    } else {
        const credPath = join(process.cwd(), '.stackowl.credentials.json');
        if (existsSync(credPath)) {
            try {
                const creds = JSON.parse(await readFile(credPath, 'utf-8')) as Record<string, string>;
                botToken = creds['telegramBotToken'] ?? '';
            } catch { }
        }
    }

    if (botToken) {
        // Start Telegram and then CLI
        await telegramCommand({ owl: opts.owl, withCli: true });
    } else {
        // No telegram, just start CLI directly
        await chatCommand(opts.owl);
    }
}

// ─── CLI Setup ───────────────────────────────────────────────────

program
    .name('stackowl')
    .description('🦉 StackOwl — Personal AI Assistant')
    .version('0.1.0');

program
    .command('chat')
    .description('Start an interactive chat session')
    .option('-o, --owl <name>', 'Owl persona to use')
    .action(async (opts: { owl?: string }) => {
        await chatCommand(opts.owl);
    });

program
    .command('telegram')
    .description('Start Telegram bot channel')
    .option('-o, --owl <name>', 'Owl persona to use')
    .option('--with-cli', 'Also start CLI chat alongside Telegram')
    .action(async (opts: { owl?: string; withCli?: boolean }) => {
        await telegramCommand(opts);
    });

program
    .command('parliament [topic]')
    .description('Convene a Parliament of owls to debate a complex topic')
    .action((topic) => {
        parliamentCommand(topic).catch((err) => {
            console.error(chalk.red(`Fatal error: ${err.message}`));
            process.exit(1);
        });
    });

program
    .command('owls')
    .description('List available owl personas')
    .action(async () => {
        await owlsCommand();
    });

program
    .command('pellets')
    .description('Manage and search Knowledge Pellets')
    .option('-s, --search <query>', 'Search pellets by keyword or tag')
    .option('-r, --read <id>', 'Read the full content of a specific pellet')
    .action((opts) => {
        pelletsCommand(opts).catch((err) => {
            console.error(chalk.red(`Fatal error: ${err.message}`));
            process.exit(1);
        });
    });

program
    .command('evolve <owlName>')
    .description('Trigger a DNA evolution pass for a specific owl')
    .action((owlName) => {
        evolveCommand(owlName).catch((err) => {
            console.error(chalk.red(`Fatal error: ${err.message}`));
            process.exit(1);
        });
    });

program
    .command('web')
    .description('Start the StackOwl Web UI Server')
    .option('-p, --port <number>', 'Port to listen on', '3000')
    .action((opts) => {
        webCommand(opts.port).catch((err) => {
            console.error(chalk.red(`Fatal error: ${err.message}`));
            process.exit(1);
        });
    });

program
    .command('status')
    .description('Show system status and provider health')
    .action(async () => {
        await statusCommand();
    });

program
    .command('all')
    .description('Start all available channels (CLI, Web, and optionally Telegram)')
    .option('-o, --owl <name>', 'Owl persona to use')
    .option('-p, --port <number>', 'Port for Web UI', '3000')
    .action((opts) => {
        allCommand(opts).catch((err) => {
            console.error(chalk.red(`Fatal error: ${err.message}`));
            process.exit(1);
        });
    });

// Default to chat if no command given
program
    .action(async () => {
        await chatCommand();
    });

program.parse();
