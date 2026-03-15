import { describe, it, expect } from 'vitest';
import * as imgConv from '../src/tools/computer-use/macos.js';

describe('Image to Text Converter', () => {
  describe('elementToBoxString', () => {
    it('should format element as box string', () => {
      const el: imgConv.ElementBox = {
        element: 'AXButton',
        role: 'button',
        text: 'Click Me',
        position: { x: 100, y: 50 },
        size: { width: 200, height: 40 }
      };

      const result = imgConv.elementToBoxString(el);
      
      expect(result).toContain('Element: AXButton');
      expect(result).toContain('role: button');
      expect(result).toContain('Click Me');
      expect(result).toMatch(/Position:/);
      expect(result).toMatch(/Size:/);
    });

    it('should truncate long text', () => {
      const el: imgConv.ElementBox = {
        element: 'AXTextField',
        role: 'textfield',
        text: 'This is a very long text that should be truncated to fit the box model representation for display purposes',
        position: { x: 0, y: 0 },
        size: { width: 100, height: 20 }
      };

      const result = imgConv.elementToBoxString(el);
      
      expect(result).toContain('...');
    });
  });

  describe('renderTerminalGrid', () => {
    it('should create grid representation of elements', () => {
      const elements: imgConv.ElementBox[] = [
        {
          element: 'window',
          role: 'AXWindow',
          position: { x: 0, y: 0 },
          size: { width: 10, height: 5 }
        }
      ];

      const grid = imgConv.renderTerminalGrid(elements, 20, 10);
      
      expect(grid).toContain('Screen: 20x10');
      expect(grid).toMatch(/█/);
    });

    it('should handle empty elements', () => {
      const grid = imgConv.renderTerminalGrid([], 10, 5);
      expect(grid).toContain('Screen: 10x5');
    });
  });

  describe('analyzeScreenshotToText', () => {
    it('should format screenshot analysis as text', () => {
      const analysis: imgConv.ScreenshotAnalysis = {
        screen: {
          resolution: { width: 1920, height: 1080 },
          scale: 2
        },
        elements: [],
        timestamp: Date.now()
      };

      const result = imgConv.analyzeScreenshotToText(analysis);
      
      expect(result).toContain('SCREEN ANALYSIS');
      expect(result).toContain('Resolution: 1920x1080');
      expect(result).toContain('Scale factor: 2x');
      expect(result).toContain('Elements found: 0');
    });

    it('should include element details when present', () => {
      const analysis: imgConv.ScreenshotAnalysis = {
        screen: {
          resolution: { width: 1920, height: 1080 },
          scale: 1
        },
        elements: [
          {
            element: 'AXButton',
            role: 'button',
            text: 'Submit',
            position: { x: 100, y: 200 },
            size: { width: 80, height: 30 }
          }
        ],
        timestamp: Date.now()
      };

      const result = imgConv.analyzeScreenshotToText(analysis);
      
      expect(result).toContain('[1] button');
      expect(result).toContain('Text: "Submit"');
      expect(result).toContain('Position: (100, 200)');
      expect(result).toContain('Size: 80x30');
    });
  });

  describe('extractScreenshotMetadata', () => {
    it('should extract file metadata', async () => {
      const fs = require('node:fs');
      const path = require('node:path');
      
      const testDir = '/tmp/stackowl-test';
      fs.mkdirSync(testDir, { recursive: true });
      
      const testFile = path.join(testDir, 'screenshot.png');
      fs.writeFileSync(testFile, Buffer.from('test image content'));
      
      const metadata = imgConv.extractScreenshotMetadata(testFile);
      
      expect(metadata.path).toContain('screenshot.png');
      expect(metadata.filename).toBe('screenshot.png');
      expect(metadata.sizeBytes).toBeGreaterThan(0);
      
      fs.unlinkSync(testFile);
    });
  });

  describe('formatScreenshotForLLM', () => {
    it('should format screenshot for LLM consumption', () => {
      const metadata = {
        path: '/tmp/test.png',
        filename: 'test.png',
        sizeBytes: 12345
      };
      
      const result = imgConv.formatScreenshotForLLM(metadata);
      
      expect(result).toContain('SCREENSHOT CAPTURED');
      expect(result).toContain('/tmp/test.png');
      expect(result).toMatch(/Size: \d+\.\d+ KB/);
    });

    it('should include analysis when provided', () => {
      const metadata = {
        path: '/tmp/test.png',
        filename: 'test.png',
        sizeBytes: 1024
      };
      
      const analysis: imgConv.ScreenshotAnalysis = {
        screen: {
          resolution: { width: 800, height: 600 },
          scale: 1
        },
        elements: [],
        timestamp: Date.now()
      };
      
      const result = imgConv.formatScreenshotForLLM(metadata, analysis);
      
      expect(result).toContain('SCREEN ANALYSIS');
      expect(result).toContain('Resolution: 800x600');
    });
  });
});
