import type { ToolImplementation, ToolContext } from "../registry.js";

export const TranslatorTool: ToolImplementation = {
  definition: {
    name: "translate",
    description:
      "Translate text between languages. Uses macOS built-in translation or shell-based approach. " +
      "Supports all major languages. Provide source and target language codes (e.g., en, es, fr, de, ja, zh, ar, ru, ko, pt, it, nl, tr, hi).",
    parameters: {
      type: "object",
      properties: {
        text: {
          type: "string",
          description: "The text to translate",
        },
        from: {
          type: "string",
          description:
            "Source language code (e.g., 'en', 'es', 'auto' for auto-detect). Default: 'auto'",
        },
        to: {
          type: "string",
          description:
            "Target language code (e.g., 'es', 'fr', 'de', 'ja'). Required.",
        },
      },
      required: ["text", "to"],
    },
  },

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    const text = String(args.text);
    const to = String(args.to).toLowerCase();
    const from = args.from ? String(args.from).toLowerCase() : "auto";

    if (!text.trim()) return "Error: No text provided to translate.";

    const { execFile } = await import("node:child_process");
    const { promisify } = await import("node:util");
    const execFileAsync = promisify(execFile);

    // Use Python's deep_translator or fallback to a JXA-based approach
    // First try: use Python translate library if available
    try {
      const escapedText = text.replace(/'/g, "\\'").replace(/"/g, '\\"');
      const pyScript = `
import json, sys
try:
    from deep_translator import GoogleTranslator
    src = '${from}' if '${from}' != 'auto' else 'auto'
    result = GoogleTranslator(source=src, target='${to}').translate("""${escapedText}""")
    print(json.dumps({"translation": result, "engine": "google"}))
except ImportError:
    # Fallback: use urllib to hit a free translation endpoint
    import urllib.request, urllib.parse
    params = urllib.parse.urlencode({
        'client': 'gtx', 'sl': '${from}', 'tl': '${to}',
        'dt': 't', 'q': """${escapedText}"""
    })
    url = f'https://translate.googleapis.com/translate_a/single?{params}'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    resp = urllib.request.urlopen(req, timeout=10)
    data = json.loads(resp.read().decode())
    translated = ''.join(seg[0] for seg in data[0] if seg[0])
    detected = data[2] if len(data) > 2 else '${from}'
    print(json.dumps({"translation": translated, "engine": "google-free", "detected_lang": detected}))
`;

      const { stdout } = await execFileAsync("python3", ["-c", pyScript], {
        timeout: 15000,
      });

      const result = JSON.parse(stdout.trim());
      const langInfo =
        from === "auto" && result.detected_lang
          ? ` (detected: ${result.detected_lang})`
          : "";

      return (
        `**Translation** (${from}${langInfo} → ${to}):\n\n` + result.translation
      );
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      return `Translation failed: ${msg}\n\nTip: Install deep_translator for best results: pip3 install deep_translator`;
    }
  },
};
