/**
 * StackOwl — Send File Tool
 *
 * Lets the owl explicitly send a file or image to the user via the active channel.
 * The actual delivery is handled by a channel-provided callback (Telegram, CLI, etc).
 *
 * Supports both local file paths AND HTTP/HTTPS URLs.
 * When a URL is provided, the image is downloaded to workspace/downloads/ first.
 */

import { existsSync, statSync } from 'node:fs';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve, isAbsolute, extname, basename, join } from 'node:path';
import type { ToolImplementation, ToolContext } from './registry.js';

const IMAGE_EXTS = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp']);
const DOC_EXTS   = new Set(['.pdf', '.csv', '.json', '.txt', '.md', '.zip', '.tar', '.gz', '.log', '.ts', '.js', '.py']);

/**
 * Detect if a string is a web URL (http:// or https://).
 */
function isWebUrl(path: string): boolean {
    return path.startsWith('http://') || path.startsWith('https://');
}

/**
 * Download a file from a URL to a local path.
 * Returns the local file path on success, or throws on failure.
 */
async function downloadToLocal(url: string, cwd: string): Promise<string> {
    const downloadsDir = join(cwd, 'workspace', 'downloads');
    await mkdir(downloadsDir, { recursive: true });

    // Parse extension from URL pathname (ignore query params)
    let urlExt = '';
    try {
        const parsed = new URL(url);
        urlExt = extname(parsed.pathname).toLowerCase();
    } catch { /* non-fatal */ }

    // Fallback extension if none detected
    if (!urlExt || urlExt.length > 6) urlExt = '.jpg';

    const filename = `download-${Date.now()}${urlExt}`;
    const localPath = join(downloadsDir, filename);

    const response = await fetch(url, {
        signal: AbortSignal.timeout(30_000),
        headers: {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        },
    });

    if (!response.ok) {
        throw new Error(`HTTP ${response.status}: Failed to download from ${url}`);
    }

    const contentType = response.headers.get('content-type') ?? '';
    // If the response isn't an image/file, bail early
    if (contentType.includes('text/html') && !contentType.includes('image')) {
        throw new Error(`URL returned HTML instead of an image. The link may be a web page, not a direct file.`);
    }

    const buffer = Buffer.from(await response.arrayBuffer());
    if (buffer.length === 0) {
        throw new Error('Downloaded file is empty (0 bytes).');
    }

    await writeFile(localPath, buffer);
    return localPath;
}

export const SendFileTool: ToolImplementation = {
    definition: {
        name: 'send_file',
        description:
            'Send a file or image to the user in the current chat. ' +
            'Use this after creating, downloading, or capturing a file that the user should receive. ' +
            'Supports images (png, jpg, gif, webp) and documents (pdf, csv, json, txt, zip, etc). ' +
            'The path can be a local file path OR an HTTP/HTTPS URL — URLs will be downloaded automatically.',
        parameters: {
            type: 'object',
            properties: {
                path: {
                    type: 'string',
                    description: 'Absolute path, workspace-relative path, or HTTP/HTTPS URL to the file to send',
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

        let resolved: string;

        // ─── Handle URL downloads ────────────────────────────────
        if (isWebUrl(rawPath)) {
            try {
                resolved = await downloadToLocal(rawPath, context.cwd || process.cwd());
            } catch (err) {
                return `Error downloading file from URL: ${err instanceof Error ? err.message : String(err)}`;
            }
        } else {
            resolved = isAbsolute(rawPath)
                ? rawPath
                : resolve(context.cwd || process.cwd(), rawPath);
        }

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
