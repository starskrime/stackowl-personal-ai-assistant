/**
 * StackOwl — Send File Tool
 *
 * Lets the owl explicitly send a file or image to the user via the active channel.
 * The actual delivery is handled by a channel-provided callback (Telegram, CLI, etc).
 */

import { existsSync, statSync } from 'node:fs';
import { resolve, isAbsolute, extname, basename } from 'node:path';
import type { ToolImplementation, ToolContext } from './registry.js';

const IMAGE_EXTS = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp']);
const DOC_EXTS   = new Set(['.pdf', '.csv', '.json', '.txt', '.md', '.zip', '.tar', '.gz', '.log', '.ts', '.js', '.py']);

export const SendFileTool: ToolImplementation = {
    definition: {
        name: 'send_file',
        description:
            'Send a file or image to the user in the current chat. ' +
            'Use this after creating, downloading, or capturing a file that the user should receive. ' +
            'Supports images (png, jpg, gif, webp) and documents (pdf, csv, json, txt, zip, etc). ' +
            'The path must point to an existing file on disk.',
        parameters: {
            type: 'object',
            properties: {
                path: {
                    type: 'string',
                    description: 'Absolute or workspace-relative path to the file to send',
                },
                caption: {
                    type: 'string',
                    description: 'Optional caption or description to send with the file',
                },
            },
            required: ['path'],
        },
    },

    async execute(args: Record<string, unknown>, context: ToolContext): Promise<string> {
        const rawPath = args['path'] as string;
        const caption = (args['caption'] as string | undefined) ?? '';

        if (!rawPath) throw new Error('path argument missing');

        const resolved = isAbsolute(rawPath)
            ? rawPath
            : resolve(context.cwd || process.cwd(), rawPath);

        if (!existsSync(resolved)) {
            return `Error: file not found at "${resolved}". Make sure the file was created before calling send_file.`;
        }

        const stat = statSync(resolved);
        if (!stat.isFile()) {
            return `Error: "${resolved}" is a directory, not a file.`;
        }

        const ext = extname(resolved).toLowerCase();
        const name = basename(resolved);
        const sizeKb = Math.round(stat.size / 1024);

        // Check channel has file sending capability
        const sendFile = context.engineContext?.sendFile;
        if (!sendFile) {
            // CLI fallback — just confirm the file exists and where it is
            const kind = IMAGE_EXTS.has(ext) ? 'image' : DOC_EXTS.has(ext) ? 'document' : 'file';
            return `[CLI] ${kind} ready at: ${resolved} (${sizeKb}KB). Open it manually — file sending is only supported in Telegram.`;
        }

        try {
            await sendFile(resolved, caption || undefined);
            const kind = IMAGE_EXTS.has(ext) ? 'image' : 'file';
            return `Successfully sent ${kind} "${name}" (${sizeKb}KB) to the user.`;
        } catch (err) {
            return `Failed to send file: ${err instanceof Error ? err.message : String(err)}`;
        }
    },
};
