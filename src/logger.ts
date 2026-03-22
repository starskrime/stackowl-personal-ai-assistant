/**
 * StackOwl — Logger
 *
 * Centralized structured logging. Shows everything:
 *   - User messages in / out of every channel
 *   - Full LLM request (every message in the payload)
 *   - Full LLM response (raw content + tool calls)
 *   - Tool calls + full results
 *   - Evolution, memory, heartbeat events
 */

import chalk from 'chalk';
import { writeFileSync, mkdirSync, appendFileSync } from 'node:fs';
import { join } from 'node:path';
import type { ChatMessage } from './providers/base.js';

// ─── File Logger ──────────────────────────────────────────────────
// Writes a plain-text session log to workspace/logs/session.log.
// Overwritten on each process start. Captures everything the console
// logger captures, but without ANSI colors — ready to share for debugging.

let _logFilePath: string | null = null;
let _logFileInitialized = false;

/** Call once at startup to enable file logging. */
export function initFileLog(workspacePath: string): void {
    const logsDir = join(workspacePath, 'logs');
    mkdirSync(logsDir, { recursive: true });
    _logFilePath = join(logsDir, 'session.log');
    // Overwrite on restart
    writeFileSync(_logFilePath, `=== StackOwl Session Log ===\nStarted: ${new Date().toISOString()}\n${'='.repeat(60)}\n\n`);
    _logFileInitialized = true;
}

/** Append a line to the session log file (no-op if not initialized). */
function fileLog(line: string): void {
    if (!_logFileInitialized || !_logFilePath) return;
    try {
        const ts = new Date().toISOString().slice(11, 23); // HH:mm:ss.SSS
        appendFileSync(_logFilePath, `[${ts}] ${line}\n`);
    } catch {
        // Non-fatal — never let logging break the app
    }
}

/** Append a multi-line block to the session log file. */
function fileLogBlock(header: string, content: string): void {
    if (!_logFileInitialized || !_logFilePath) return;
    try {
        const ts = new Date().toISOString().slice(11, 23);
        const bar = '─'.repeat(60);
        appendFileSync(_logFilePath, `\n[${ts}] ${bar}\n  ${header}\n${bar}\n${content}\n${bar}\n\n`);
    } catch {
        // Non-fatal
    }
}

// ─── Log Levels ───────────────────────────────────────────────────

export type LogLevel = 'debug' | 'info' | 'warn' | 'error';

const LEVEL_CONFIG: Record<LogLevel, { label: string; color: (s: string) => string }> = {
    debug: { label: 'DBG', color: chalk.gray },
    info:  { label: 'INF', color: chalk.cyan },
    warn:  { label: 'WRN', color: chalk.yellow },
    error: { label: 'ERR', color: chalk.red },
};

// ─── Module Colors ────────────────────────────────────────────────

const MODULE_COLORS: Record<string, (s: string) => string> = {
    TELEGRAM:   chalk.blue,
    CLI:        chalk.greenBright,
    ENGINE:     chalk.magenta,
    TOOL:       chalk.green,
    EVOLUTION:  chalk.yellow,
    MEMORY:     chalk.cyan,
    HEARTBEAT:  chalk.gray,
    PELLET:     chalk.white,
    DEFAULT:    chalk.white,
};

function moduleColor(mod: string): (s: string) => string {
    return MODULE_COLORS[mod.toUpperCase()] ?? MODULE_COLORS.DEFAULT;
}

// ─── Helpers ──────────────────────────────────────────────────────

function timestamp(): string {
    const now = new Date();
    const h = String(now.getHours()).padStart(2, '0');
    const m = String(now.getMinutes()).padStart(2, '0');
    const s = String(now.getSeconds()).padStart(2, '0');
    const ms = String(now.getMilliseconds()).padStart(3, '0');
    return chalk.dim(`[${h}:${m}:${s}.${ms}]`);
}


function printBlock(label: string, labelColor: (s: string) => string, content: string): void {
    const width = 72;
    const bar = chalk.dim('─'.repeat(width));
    console.log(`\n${bar}`);
    console.log(labelColor(`  ${label}`));
    console.log(bar);
    // Print content indented, preserving newlines
    for (const line of content.split('\n')) {
        console.log(`  ${line}`);
    }
    console.log(`${bar}\n`);
}

// ─── Logger Class ─────────────────────────────────────────────────

export class Logger {
    private module: string;
    private colorFn: (s: string) => string;

    constructor(module: string) {
        this.module = module.toUpperCase();
        this.colorFn = moduleColor(module);
    }

    private prefix(level: LogLevel): string {
        const cfg = LEVEL_CONFIG[level];
        const mod = this.colorFn(`[${this.module}]`);
        const lvl = cfg.color(`[${cfg.label}]`);
        return `${timestamp()} ${mod} ${lvl}`;
    }

    debug(msg: string, ...extra: unknown[]): void {
        fileLog(`[${this.module}] [DBG] ${msg}`);
        if (process.env.STACKOWL_LOG_LEVEL === 'debug') {
            console.debug(`${this.prefix('debug')} ${chalk.gray(msg)}`, ...extra);
        }
    }

    info(msg: string, ...extra: unknown[]): void {
        fileLog(`[${this.module}] [INF] ${msg}`);
        console.log(`${this.prefix('info')} ${msg}`, ...extra);
    }

    warn(msg: string, ...extra: unknown[]): void {
        fileLog(`[${this.module}] [WRN] ${msg}`);
        console.warn(`${this.prefix('warn')} ${chalk.yellow(msg)}`, ...extra);
    }

    error(msg: string, ...extra: unknown[]): void {
        fileLog(`[${this.module}] [ERR] ${msg}`);
        console.error(`${this.prefix('error')} ${chalk.red(msg)}`, ...extra);
    }

    // ─── Channel I/O ──────────────────────────────────────────────

    /** Full incoming message from a user (channel → engine) */
    incoming(from: string, text: string): void {
        fileLogBlock(`← USER [${from}]`, text);
        printBlock(
            `← USER  [${from}]`,
            chalk.bold.green,
            text
        );
    }

    /** Full outgoing response to a user (engine → channel) */
    outgoing(to: string, text: string): void {
        fileLogBlock(`→ OWL [${to}]`, text);
        printBlock(
            `→ OWL   [${to}]`,
            chalk.bold.blue,
            text
        );
    }

    // ─── LLM I/O ─────────────────────────────────────────────────

    /** Full payload sent to the LLM */
    llmRequest(model: string, messages: ChatMessage[]): void {
        // File log: plain text summary (no ANSI)
        const fileLines: string[] = [`Model: ${model}  |  Messages: ${messages.length}`];
        for (const msg of messages) {
            const role = (msg.role as string).toUpperCase();
            const toolName = (msg as any).name ? `:${(msg as any).name}` : '';
            let body = (msg.content || '').slice(0, 500);
            if (msg.toolCalls && msg.toolCalls.length > 0) {
                const calls = msg.toolCalls.map(t => `  call: ${t.name} ${JSON.stringify(t.arguments)}`).join('\n');
                body = body ? `${body}\n${calls}` : calls;
            }
            fileLines.push(`[${role}${toolName}] ${body || '(empty)'}`);
        }
        fileLogBlock('→ LLM REQUEST', fileLines.join('\n'));

        // Console log: colorized
        const lines: string[] = [];
        lines.push(chalk.cyan(`Model: ${model}  |  Messages: ${messages.length}`));
        lines.push('');
        for (const msg of messages) {
            const roleStr = (msg.role as string).toUpperCase();
            const roleLabel = msg.role === 'system'    ? chalk.dim.italic('[SYSTEM]')
                            : msg.role === 'user'      ? chalk.green('[USER]')
                            : msg.role === 'assistant' ? chalk.yellow('[ASSISTANT]')
                            : msg.role === 'tool'      ? chalk.blue(`[TOOL:${(msg as any).name ?? '?'}]`)
                            : chalk.gray(`[${roleStr}]`);

            let body = msg.content || '';
            if (msg.toolCalls && msg.toolCalls.length > 0) {
                const callLines = msg.toolCalls.map(t =>
                    `  call: ${chalk.yellow(t.name)}  ${JSON.stringify(t.arguments)}`
                ).join('\n');
                body = body ? `${body}\n${callLines}` : callLines;
            }
            if (!body) body = '(empty)';

            lines.push(roleLabel);
            lines.push(body);
            lines.push('');
        }
        printBlock(`→ LLM REQUEST`, chalk.bold.magenta, lines.join('\n'));
    }

    /** Full response received from the LLM */
    llmResponse(model: string, content: string, toolCalls?: Array<{ name: string; arguments: Record<string, unknown> }>, usage?: { promptTokens: number; completionTokens: number }): void {
        // File log
        const fileParts: string[] = [];
        const tokenStr = usage ? ` [${usage.promptTokens}→${usage.completionTokens} tokens]` : '';
        fileParts.push(`Model: ${model}${tokenStr}`);
        if (content) fileParts.push(`[CONTENT] ${content.slice(0, 1000)}`);
        if (toolCalls && toolCalls.length > 0) {
            fileParts.push('[TOOL CALLS]');
            for (const tc of toolCalls) {
                fileParts.push(`  ${tc.name} ${JSON.stringify(tc.arguments)}`);
            }
        }
        fileLogBlock('← LLM RESPONSE', fileParts.join('\n'));

        // Console log
        const lines: string[] = [];
        const tokens = usage ? chalk.dim(` [${usage.promptTokens}→${usage.completionTokens} tokens]`) : '';
        lines.push(chalk.cyan(`Model: ${model}${tokens}`));
        lines.push('');

        if (content) {
            lines.push(chalk.yellow('[CONTENT]'));
            lines.push(content);
        }

        if (toolCalls && toolCalls.length > 0) {
            lines.push('');
            lines.push(chalk.green('[TOOL CALLS]'));
            for (const tc of toolCalls) {
                lines.push(`  ${chalk.bold(tc.name)}  ${JSON.stringify(tc.arguments)}`);
            }
        }

        printBlock(`← LLM RESPONSE`, chalk.bold.magenta, lines.join('\n'));
    }

    // ─── Tool I/O ─────────────────────────────────────────────────

    /** Tool being called */
    toolCall(name: string, args?: Record<string, unknown>): void {
        const argsStr = args && Object.keys(args).length > 0
            ? '\n  ' + JSON.stringify(args, null, 2).replace(/\n/g, '\n  ')
            : '';
        fileLog(`[TOOL] CALL ${name} ${args ? JSON.stringify(args) : ''}`);
        console.log(`${this.prefix('info')} ${chalk.bold.yellow('⚙')} ${chalk.yellow(`CALL  ${name}`)}${argsStr}`);
    }

    /** Full tool result */
    toolResult(name: string, result: string, success: boolean): void {
        const icon = success ? chalk.green('✓') : chalk.red('✗');
        const label = `${icon} ${chalk.dim(`RESULT ${name}`)}`;
        fileLog(`[TOOL] RESULT ${name} ${success ? '✓' : '✗'} ${result.slice(0, 500)}`);
        // Short result: inline. Long result: block.
        if (result.length <= 200) {
            console.log(`${this.prefix('info')} ${label}  ${chalk.dim(result)}`);
        } else {
            printBlock(`TOOL RESULT  ${name}  ${success ? '✓' : '✗'}`, success ? chalk.green : chalk.red, result);
        }
    }

    // ─── Misc ─────────────────────────────────────────────────────

    /** Model selection / routing */
    model(selected: string, reason?: string): void {
        const r = reason ? chalk.dim(` (${reason})`) : '';
        console.log(`${this.prefix('info')} ${chalk.dim('model →')} ${chalk.cyan(selected)}${r}`);
    }

    /** Evolution / synthesis event */
    evolve(msg: string): void {
        fileLog(`[${this.module}] 🧬 ${msg}`);
        console.log(`${this.prefix('info')} ${chalk.bold.magenta('🧬')} ${chalk.magenta(msg)}`);
    }

    /** Separator for visual grouping */
    separator(): void {
        console.log(chalk.dim('  ' + '─'.repeat(60)));
    }
}

// ─── Singletons ───────────────────────────────────────────────────

export const log = {
    telegram:  new Logger('TELEGRAM'),
    slack:     new Logger('SLACK'),
    cli:       new Logger('CLI'),
    engine:    new Logger('ENGINE'),
    tool:      new Logger('TOOL'),
    evolution: new Logger('EVOLUTION'),
    memory:    new Logger('MEMORY'),
    heartbeat: new Logger('HEARTBEAT'),
    pellet:    new Logger('PELLET'),
};
