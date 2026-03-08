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
import type { ChatMessage } from './providers/base.js';
import { TelegramChannel } from './channels/telegram.js';
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

    return { config, providerRegistry, owlRegistry, engine, workspacePath };
}

// ─── Chat Command ────────────────────────────────────────────────

async function chatCommand(owlName?: string) {
    console.log(BANNER);

    const { providerRegistry, owlRegistry, engine, config } = await bootstrap();

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
        chalk.dim(`\nType your message. Use ${chalk.bold('/quit')} to exit, ${chalk.bold('/owls')} to list owls.\n`)
    );

    const sessionHistory: ChatMessage[] = [];

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

        // Send to engine
        try {
            process.stdout.write(chalk.yellow(`\n${owl.persona.emoji} ${owl.persona.name}: `));

            const response = await engine.run(input, {
                provider,
                owl,
                sessionHistory,
                model: config.defaultModel,
            });

            console.log(response.content);

            // Update session history
            sessionHistory.push({ role: 'user', content: input });
            sessionHistory.push({ role: 'assistant', content: response.content });

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

// ─── Telegram Command ────────────────────────────────────────────

async function telegramCommand(opts: { owl?: string; withCli?: boolean }) {
    console.log(BANNER);

    const { providerRegistry, owlRegistry, config } = await bootstrap();

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

    const telegram = new TelegramChannel({
        botToken,
        provider,
        owl,
        model: config.defaultModel,
    });

    // Graceful shutdown
    const shutdown = () => {
        console.log(chalk.dim('\n🦉 Shutting down Telegram bot...'));
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
    .command('owls')
    .description('List available owl personas')
    .action(async () => {
        await owlsCommand();
    });

program
    .command('status')
    .description('Show system status and provider health')
    .action(async () => {
        await statusCommand();
    });

// Default to chat if no command given
program
    .action(async () => {
        await chatCommand();
    });

program.parse();
