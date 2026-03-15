import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { loadConfig } from '../src/config/loader.js';
import { writeFile, rm, mkdir } from 'node:fs/promises';
import { join } from 'node:path';

describe('Config Validation', () => {
    const testDir = join(__dirname, '.test_config');

    beforeEach(async () => {
        await rm(testDir, { recursive: true, force: true }).catch(() => {});
        await mkdir(testDir, { recursive: true });
    });

    afterEach(async () => {
        await rm(testDir, { recursive: true, force: true }).catch(() => {});
    });

    it('should create default config when no file exists', async () => {
        const config = await loadConfig(testDir);
        expect(config.defaultProvider).toBe('ollama');
        expect(config.defaultModel).toBe('llama3.2');
        expect(config.gateway.port).toBe(3077);
    });

    it('should deep-merge user config with defaults', async () => {
        await writeFile(
            join(testDir, 'stackowl.config.json'),
            JSON.stringify({
                defaultProvider: 'openai',
                defaultModel: 'gpt-4',
                providers: {
                    openai: { baseUrl: 'https://api.openai.com', apiKey: 'sk-test' },
                },
            }),
            'utf-8',
        );

        const config = await loadConfig(testDir);
        expect(config.defaultProvider).toBe('openai');
        expect(config.defaultModel).toBe('gpt-4');
        // Defaults should still be present
        expect(config.gateway.port).toBe(3077);
        expect(config.owlDna.enabled).toBe(true);
    });

    it('should warn on invalid port range', async () => {
        const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});

        await writeFile(
            join(testDir, 'stackowl.config.json'),
            JSON.stringify({
                defaultProvider: 'ollama',
                defaultModel: 'llama3.2',
                providers: { ollama: { baseUrl: 'http://127.0.0.1:11434' } },
                gateway: { port: 99999, host: '127.0.0.1' },
            }),
            'utf-8',
        );

        await loadConfig(testDir);
        expect(warnSpy).toHaveBeenCalled();
        const warnCall = warnSpy.mock.calls[0]?.[0] as string;
        expect(warnCall).toContain('gateway.port');

        warnSpy.mockRestore();
    });

    it('should warn when defaultProvider is not in providers', async () => {
        const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});

        await writeFile(
            join(testDir, 'stackowl.config.json'),
            JSON.stringify({
                defaultProvider: 'nonexistent',
                defaultModel: 'test',
                providers: { ollama: { baseUrl: 'http://127.0.0.1:11434' } },
            }),
            'utf-8',
        );

        await loadConfig(testDir);
        expect(warnSpy).toHaveBeenCalled();
        const warnCall = warnSpy.mock.calls[0]?.[0] as string;
        expect(warnCall).toContain('nonexistent');

        warnSpy.mockRestore();
    });

    it('should warn on high maxToolIterations', async () => {
        const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});

        await writeFile(
            join(testDir, 'stackowl.config.json'),
            JSON.stringify({
                defaultProvider: 'ollama',
                defaultModel: 'llama3.2',
                providers: { ollama: { baseUrl: 'http://127.0.0.1:11434' } },
                engine: { maxToolIterations: 100 },
            }),
            'utf-8',
        );

        await loadConfig(testDir);
        expect(warnSpy).toHaveBeenCalled();
        const warnCall = warnSpy.mock.calls[0]?.[0] as string;
        expect(warnCall).toContain('maxToolIterations');

        warnSpy.mockRestore();
    });

    it('should throw on invalid JSON', async () => {
        await writeFile(join(testDir, 'stackowl.config.json'), 'not json', 'utf-8');
        await expect(loadConfig(testDir)).rejects.toThrow('Failed to load');
    });

    it('should warn when skills enabled without directories', async () => {
        const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});

        await writeFile(
            join(testDir, 'stackowl.config.json'),
            JSON.stringify({
                defaultProvider: 'ollama',
                defaultModel: 'llama3.2',
                providers: { ollama: { baseUrl: 'http://127.0.0.1:11434' } },
                skills: { enabled: true, directories: [] },
            }),
            'utf-8',
        );

        await loadConfig(testDir);
        expect(warnSpy).toHaveBeenCalled();
        const warnCall = warnSpy.mock.calls[0]?.[0] as string;
        expect(warnCall).toContain('skills');

        warnSpy.mockRestore();
    });
});

// Vitest auto-imports `vi`
import { vi } from 'vitest';
