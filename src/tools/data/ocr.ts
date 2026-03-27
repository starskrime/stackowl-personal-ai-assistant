import type { ToolImplementation, ToolContext } from "../registry.js";

export const OCRTool: ToolImplementation = {
  definition: {
    name: "ocr",
    description:
      "Extract text from images using OCR (Optical Character Recognition). " +
      "Uses macOS Vision framework — works offline, no API needed. " +
      "Supports PNG, JPG, TIFF, BMP, and screenshots.",
    parameters: {
      type: "object",
      properties: {
        path: {
          type: "string",
          description: "Path to the image file",
        },
        language: {
          type: "string",
          description:
            "Recognition language: en (default), zh, ja, ko, de, fr, es, pt, it, ru",
        },
      },
      required: ["path"],
    },
  },

  category: "filesystem",

  async execute(
    args: Record<string, unknown>,
    context: ToolContext,
  ): Promise<string> {
    const filePath = String(args.path);
    const language = (args.language as string) || "en";
    const cwd = context.cwd || process.cwd();

    const { execFile } = await import("node:child_process");
    const { promisify } = await import("node:util");
    const { resolve } = await import("node:path");
    const { existsSync } = await import("node:fs");
    const exec = promisify(execFile);

    const resolvedPath = resolve(cwd, filePath);
    if (!existsSync(resolvedPath)) {
      return `Error: File not found: ${resolvedPath}`;
    }

    // Method 1: macOS Vision framework via Swift
    try {
      const swiftScript = `
import Vision
import Foundation
import AppKit

let url = URL(fileURLWithPath: "${resolvedPath}")
guard let image = NSImage(contentsOf: url),
      let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    print("ERROR: Cannot load image")
    exit(1)
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = ["${language}"]
request.usesLanguageCorrection = true

let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
try handler.perform([request])

guard let observations = request.results else {
    print("No text found")
    exit(0)
}

for observation in observations {
    if let candidate = observation.topCandidates(1).first {
        print(candidate.string)
    }
}
`;
      const { stdout } = await exec("swift", ["-e", swiftScript], {
        timeout: 30000,
      });

      const text = stdout.trim();
      if (text && !text.startsWith("ERROR:")) {
        return `📷 OCR Result (${resolvedPath}):\n\n${text}`;
      }
    } catch {
      // Swift approach failed — try alternatives
    }

    // Method 2: macOS shortcuts (Ventura+)
    try {
      const { stdout } = await exec(
        "bash",
        [
          "-c",
          `shortcuts run "Extract Text from Image" -i "${resolvedPath}" 2>/dev/null`,
        ],
        { timeout: 15000 },
      );
      if (stdout.trim()) {
        return `📷 OCR Result (${resolvedPath}):\n\n${stdout.trim()}`;
      }
    } catch {
      /* try next */
    }

    // Method 3: Python tesseract
    try {
      const { stdout } = await exec(
        "python3",
        [
          "-c",
          `
import pytesseract
from PIL import Image
img = Image.open("${resolvedPath}")
text = pytesseract.image_to_string(img, lang="${language}")
print(text)
`,
        ],
        { timeout: 20000 },
      );
      if (stdout.trim()) {
        return `📷 OCR Result (${resolvedPath}):\n\n${stdout.trim()}`;
      }
    } catch {
      /* try next */
    }

    // Method 4: tesseract CLI directly
    try {
      const { stdout } = await exec(
        "tesseract",
        [resolvedPath, "stdout", "-l", language],
        { timeout: 20000 },
      );
      if (stdout.trim()) {
        return `📷 OCR Result (${resolvedPath}):\n\n${stdout.trim()}`;
      }
    } catch {
      /* fallthrough */
    }

    return (
      `Could not perform OCR. Available options:\n` +
      `  1. macOS Vision framework (should work on macOS 10.15+)\n` +
      `  2. brew install tesseract\n` +
      `  3. pip3 install pytesseract Pillow`
    );
  },
};
