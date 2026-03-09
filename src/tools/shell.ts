/**
 * StackOwl — Shell Tool
 *
 * Allows owls to execute terminal commands.
 */

import { exec } from 'node:child_process';
import { promisify } from 'node:util';
import type { ToolImplementation, ToolContext } from './registry.js';

const execAsync = promisify(exec);

const ESCAPE_PATTERNS = ['../..', '/etc/', '/var/', '/root/', '/home/'];

export const ShellTool: ToolImplementation = {
    definition: {
        name: 'run_shell_command',
        description: 'Execute a shell command. Use this to compile code, run tests, list files, run git commands, etc. TIP: Always adapt your commands to the "Host Environment" (OS Platform) specified in your system prompt. When inspecting network ports, ALWAYS use flags that disable port-to-service name resolution (e.g., `lsof -i -P -n` on macOS/Linux or `netstat -ano` on Windows) so you see raw port numbers instead of confusing service aliases (e.g., seeing "hbci" instead of "3000").',
        parameters: {
            type: 'object',
            properties: {
                command: {
                    type: 'string',
                    description: 'The shell command to execute',
                },
            },
            required: ['command'],
        },
    },

    async execute(args: Record<string, unknown>, context: ToolContext): Promise<string> {
        const cmd = args['command'] as string;
        if (!cmd) throw new Error('Command argument missing');

        // Warn (but don't block) on potential path escapes
        const hasEscape = ESCAPE_PATTERNS.some(p => cmd.includes(p));
        if (hasEscape) {
            console.warn(`[ShellTool] WARNING: command may access paths outside workspace: ${cmd.slice(0, 100)}`);
        }

        try {
            const { stdout, stderr } = await execAsync(cmd, { cwd: context.cwd });

            const stdoutTrimmed = stdout.length > 8000
                ? stdout.substring(0, 4000) + '\n...[stdout truncated]...\n' + stdout.substring(stdout.length - 4000)
                : stdout;

            const stderrTrimmed = stderr.length > 2000
                ? stderr.substring(0, 2000) + '\n...[stderr truncated]...'
                : stderr;

            return [
                `EXIT_CODE: 0`,
                `STDOUT:\n${stdoutTrimmed || '(none)'}`,
                `STDERR:\n${stderrTrimmed || '(none)'}`,
            ].join('\n\n');
        } catch (error: any) {
            const stdoutTrimmed = (error.stdout || '').length > 4000
                ? (error.stdout as string).substring(0, 4000) + '\n...[truncated]'
                : (error.stdout || '');
            const stderrTrimmed = (error.stderr || '').length > 4000
                ? (error.stderr as string).substring(0, 4000) + '\n...[truncated]'
                : (error.stderr || '');

            return [
                `EXIT_CODE: ${error.code ?? 1}`,
                `STDOUT:\n${stdoutTrimmed || '(none)'}`,
                `STDERR:\n${stderrTrimmed || '(none)'}`,
            ].join('\n\n');
        }
    },
};
