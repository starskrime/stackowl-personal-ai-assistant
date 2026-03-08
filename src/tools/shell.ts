/**
 * StackOwl — Shell Tool
 *
 * Allows owls to execute terminal commands.
 */

import { exec } from 'node:child_process';
import { promisify } from 'node:util';
import type { ToolImplementation, ToolContext } from './registry.js';

const execAsync = promisify(exec);

export const ShellTool: ToolImplementation = {
    definition: {
        name: 'run_shell_command',
        description: 'Execute a shell command. Use this to compile code, run tests, listing files, git commands, etc.',
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

        try {
            const { stdout, stderr } = await execAsync(cmd, { cwd: context.cwd });
            let output = stdout;
            if (stderr) {
                output += `\n[Stderr]:\n${stderr}`;
            }

            // Truncate if too long (e.g. 10k chars max)
            if (output.length > 10000) {
                return output.substring(0, 5000) + '\n...[output truncated]...\n' + output.substring(output.length - 5000);
            }

            return output || '(Command completed with no output)';
        } catch (error: any) {
            let errorResp = `Command failed with error code ${error.code || 'unknown'}:`;
            if (error.stdout) errorResp += `\n[Stdout]:\n${error.stdout}`;
            if (error.stderr) errorResp += `\n[Stderr]:\n${error.stderr}`;
            return errorResp;
        }
    },
};
