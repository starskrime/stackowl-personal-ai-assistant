/**
 * StackOwl — Shell Tool (Zero-Trust Sandboxed)
 *
 * Allows owls to execute terminal commands safely inside ephemeral Docker containers.
 *
 * Output capture: uses child_process.spawn (not dockerode stream API) so stdout/stderr
 * are always returned to the LLM, not just printed to the terminal.
 */

import { spawn, exec } from 'node:child_process';
import { promisify } from 'node:util';
import type { ToolImplementation, ToolContext } from './registry.js';
import { log } from '../logger.js';
import { resolve } from 'node:path';

const execAsync = promisify(exec);

const SANDBOX_IMAGE = 'node:22-alpine';
const EXEC_TIMEOUT_MS = 30_000;
const IMAGE_PULL_TIMEOUT_MS = 120_000;

// ─── Pre-flight: Network Command Detection ────────────────────────────────────
// The sandbox runs with --network none, so any command that requires internet
// access will silently fail. Detect these upfront and advise the LLM immediately.

const NETWORK_FETCH_PATTERNS = [
    /\b(curl|wget)\s+https?:\/\//,
    /\bfetch\s+['"]https?:\/\//,
];

function detectNetworkFetch(cmd: string): string | null {
    if (NETWORK_FETCH_PATTERNS.some(p => p.test(cmd))) {
        return (
            `[SYSTEM DIAGNOSTIC HINT: The sandbox has no internet access (--network none). ` +
            `You cannot use curl/wget to fetch URLs inside the sandbox. ` +
            `Use the 'web_crawl' tool to fetch any web page or URL instead. ` +
            `If you need both fetched content and shell processing, fetch with web_crawl first, ` +
            `then pass the result into a shell command via stdin or a temp file.]`
        );
    }
    return null;
}

// ─── Docker Spawn (proper stdout/stderr capture) ──────────────────────────────

interface DockerResult {
    exitCode: number;
    stdout: string;
    stderr: string;
}

function runInDocker(cmd: string, workspaceDir: string): Promise<DockerResult> {
    return new Promise((resolvePromise, reject) => {
        const proc = spawn('docker', [
            'run', '--rm',
            '--volume', `${workspaceDir}:/workspace`,
            '--workdir', '/workspace',
            '--network', 'none',
            '--env', 'NODE_ENV=development',
            SANDBOX_IMAGE,
            'sh', '-c', cmd,   // cmd passed as single arg — no shell escaping needed
        ]);

        let stdout = '';
        let stderr = '';

        proc.stdout.on('data', (chunk: Buffer) => { stdout += chunk.toString(); });
        proc.stderr.on('data', (chunk: Buffer) => { stderr += chunk.toString(); });

        const timer = setTimeout(() => {
            proc.kill('SIGKILL');
            reject(new Error(`Sandbox timed out after ${EXEC_TIMEOUT_MS / 1000}s`));
        }, EXEC_TIMEOUT_MS);

        proc.on('close', (code) => {
            clearTimeout(timer);
            resolvePromise({ exitCode: code ?? 0, stdout, stderr });
        });

        proc.on('error', (err) => {
            clearTimeout(timer);
            reject(err);
        });
    });
}

// ─── Tier 1 Auto-Heal: Pull missing image ────────────────────────────────────

async function ensureImage(): Promise<void> {
    log.tool.warn(`[ShellTool] Image '${SANDBOX_IMAGE}' not found. Auto-pulling (Tier 1 heal)...`);
    await execAsync(`docker pull ${SANDBOX_IMAGE}`, { timeout: IMAGE_PULL_TIMEOUT_MS });
    log.tool.info(`[ShellTool] Image '${SANDBOX_IMAGE}' pulled successfully.`);
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function cap(s: string, max: number): string {
    return s.length > max
        ? s.slice(0, max) + `\n...[truncated — ${s.length - max} chars omitted]`
        : s;
}

function formatResult(
    exitCode: number,
    stdout: string,
    stderr: string,
    diagnosticHint = '',
): string {
    return [
        `EXIT_CODE: ${exitCode}`,
        `STDOUT:\n${cap(stdout, 6000) || '(none)'}`,
        `STDERR:\n${cap(stderr, 2000) || '(none)'}${diagnosticHint ? '\n\n' + diagnosticHint : ''}`,
    ].join('\n\n');
}

function buildDiagnosticHint(exitCode: number, stdout: string, stderr: string): string {
    const combined = stdout + stderr;

    if (exitCode === 127 || combined.includes('not found') || combined.includes('No such file or directory')) {
        return (
            `[SYSTEM DIAGNOSTIC HINT: A command was not found in the Alpine Linux sandbox. ` +
            `Alpine only includes busybox utilities by default — no curl, wget, git, python, etc. ` +
            `To install a missing package: chain 'apk add <pkg> && <your command>' in one command. ` +
            `To fetch URLs, use the 'web_crawl' tool instead of curl/wget.]`
        );
    }
    if (exitCode === 126) {
        return `[SYSTEM DIAGNOSTIC HINT: Permission denied. Check file permissions (chmod +x) before executing.]`;
    }
    if (combined.toLowerCase().includes('out of memory') || combined.includes('Killed')) {
        return `[SYSTEM DIAGNOSTIC HINT: Process was killed (OOM or timeout). Try a smaller input or break the task into steps.]`;
    }
    return `[SYSTEM DIAGNOSTIC HINT: Non-zero exit code ${exitCode}. Check the stderr above for the root cause.]`;
}

// ─── Raw (unsandboxed) fallback ───────────────────────────────────────────────

async function executeRawCommand(cmd: string, cwd: string): Promise<string> {
    try {
        const { stdout, stderr } = await execAsync(cmd, { cwd, timeout: EXEC_TIMEOUT_MS });
        return formatResult(0, stdout, stderr);
    } catch (error: any) {
        return formatResult(
            error.code ?? 1,
            error.stdout ?? '',
            error.stderr ?? '',
        );
    }
}

// ─── Tool ────────────────────────────────────────────────────────────────────

export const ShellTool: ToolImplementation = {
    definition: {
        name: 'run_shell_command',
        description: 'Execute a shell command in an isolated Alpine Linux sandbox (no internet access). Use this to safely run code, evaluate logic, or process files. NOTE: The sandbox has NO network access — use the web_crawl tool to fetch URLs instead of curl/wget. To install a missing package, chain apk add before your command in the same shell invocation.',
        parameters: {
            type: 'object',
            properties: {
                command: {
                    type: 'string',
                    description: 'The shell command to execute inside the Alpine sandbox.',
                },
            },
            required: ['command'],
        },
    },

    async execute(args: Record<string, unknown>, context: ToolContext): Promise<string> {
        const cmd = args['command'] as string;
        if (!cmd) throw new Error('Command argument missing');

        const useSandbox = context.engineContext?.config?.sandboxing?.enabled ?? true;
        const workspaceDir = resolve(context.cwd);

        if (!useSandbox) {
            log.tool.warn(`[ShellTool] WARNING: Executing outside sandbox: ${cmd}`);
            return executeRawCommand(cmd, workspaceDir);
        }

        // ── Pre-flight: detect network fetch commands immediately ──
        const networkHint = detectNetworkFetch(cmd);
        if (networkHint) {
            log.tool.warn(`[ShellTool] Network command blocked pre-flight: ${cmd.slice(0, 80)}`);
            return formatResult(126, '', 'Network fetch commands are not supported in the sandbox.', networkHint);
        }

        log.tool.info(`[ShellTool] Executing in sandbox: ${cmd}`);

        try {
            const result = await runInDocker(cmd, workspaceDir);

            if (result.exitCode !== 0) {
                const hint = buildDiagnosticHint(result.exitCode, result.stdout, result.stderr);
                log.tool.warn(`[ShellTool] Command exited with code ${result.exitCode}`);
                return formatResult(result.exitCode, result.stdout, result.stderr, hint);
            }

            return formatResult(0, result.stdout, result.stderr);

        } catch (spawnError: any) {
            const msg: string = spawnError.message ?? String(spawnError);

            // ── Tier 1 Auto-Heal: missing Docker image ──
            if (msg.includes('Unable to find image') || msg.includes('No such image') || msg.includes('pull access denied')) {
                try {
                    await ensureImage();
                    const retryResult = await runInDocker(cmd, workspaceDir);
                    if (retryResult.exitCode !== 0) {
                        const hint = buildDiagnosticHint(retryResult.exitCode, retryResult.stdout, retryResult.stderr);
                        return formatResult(retryResult.exitCode, retryResult.stdout, retryResult.stderr, hint);
                    }
                    return formatResult(0, retryResult.stdout, retryResult.stderr);
                } catch (pullErr: any) {
                    log.tool.error(`[ShellTool] Auto-heal pull failed:`, pullErr);
                    return formatResult(1, '', String(pullErr.message ?? pullErr),
                        `[SYSTEM DIAGNOSTIC HINT: Docker image pull failed. Docker daemon may be unavailable or the image registry is unreachable. Consider disabling sandboxing in config.]`
                    );
                }
            }

            // ── Docker daemon not running → raw fallback ──
            if (msg.includes('Cannot connect to the Docker daemon') || msg.includes('connect ENOENT') || msg.includes('ENOENT')) {
                log.tool.warn(`[ShellTool] Docker daemon unavailable. Falling back to raw host execution.`);
                return executeRawCommand(cmd, workspaceDir);
            }

            // ── Timeout ──
            if (msg.includes('timed out')) {
                return formatResult(124, '', msg,
                    `[SYSTEM DIAGNOSTIC HINT: Command timed out after ${EXEC_TIMEOUT_MS / 1000}s. Break long-running tasks into smaller steps or increase the timeout in config.]`
                );
            }

            log.tool.error(`[ShellTool] Unexpected spawn error:`, spawnError);
            return formatResult(1, '', msg,
                `[SYSTEM DIAGNOSTIC HINT: Unexpected sandbox error. Check Docker is running on this machine.]`
            );
        }
    },
};
