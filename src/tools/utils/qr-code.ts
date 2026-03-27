import type { ToolImplementation, ToolContext } from "../registry.js";
import { resolve, join } from "node:path";
import { existsSync, mkdirSync } from "node:fs";

export const QRCodeTool: ToolImplementation = {
  definition: {
    name: "qr_code",
    description:
      "Generate QR codes from text, URLs, WiFi credentials, contacts, or any data. " +
      "Saves as PNG image. Use send_file to deliver the QR code image to the user.",
    parameters: {
      type: "object",
      properties: {
        data: {
          type: "string",
          description:
            "The data to encode. For WiFi: 'WIFI:T:WPA;S:NetworkName;P:password;;' " +
            "For URLs: just the URL. For contacts: vCard format.",
        },
        filename: {
          type: "string",
          description:
            "Output filename (without path). Default: qr_<timestamp>.png",
        },
        size: {
          type: "number",
          description: "QR code size in pixels (width=height). Default: 400",
        },
      },
      required: ["data"],
    },
  },

  async execute(
    args: Record<string, unknown>,
    context: ToolContext,
  ): Promise<string> {
    const data = String(args.data);
    const size = (args.size as number) || 400;
    const filename = (args.filename as string) || `qr_${Date.now()}.png`;
    const cwd = context.cwd || process.cwd();

    if (!data.trim()) return "Error: No data provided for QR code.";

    const outDir = resolve(cwd, "workspace", "qr-codes");
    if (!existsSync(outDir)) mkdirSync(outDir, { recursive: true });
    const outPath = join(outDir, filename);

    const { execFile } = await import("node:child_process");
    const { promisify } = await import("node:util");
    const execFileAsync = promisify(execFile);

    // Try Python qrcode library first, then segno, then fallback to CoreImage
    try {
      const escapedData = data.replace(/'/g, "\\'");
      const pyScript = `
import sys
try:
    import qrcode
    qr = qrcode.QRCode(version=None, box_size=10, border=4, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data('${escapedData}')
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img = img.resize((${size}, ${size}))
    img.save('${outPath}')
    print('ok:qrcode')
except ImportError:
    try:
        import segno
        qr = segno.make('${escapedData}')
        qr.save('${outPath}', scale=max(1, ${size} // 40), border=4)
        print('ok:segno')
    except ImportError:
        print('no-lib')
        sys.exit(1)
`;
      const { stdout } = await execFileAsync("python3", ["-c", pyScript], {
        timeout: 10000,
      });

      if (stdout.trim().startsWith("ok:")) {
        return (
          `QR code generated: ${outPath}\n` +
          `Data: ${data.length > 100 ? data.slice(0, 100) + "..." : data}\n` +
          `Size: ${size}x${size}px\n` +
          `Use send_file to deliver this to the user.`
        );
      }
    } catch {
      // Python libs not available — try CoreImage
    }

    // Fallback: use CIQRCodeGenerator via JXA
    try {
      const jxaScript = `
const app = Application.currentApplication();
app.includeStandardAdditions = true;
const script = \`
import Cocoa
import CoreImage

let data = "${data.replace(/"/g, '\\"')}".data(using: .utf8)!
let filter = CIFilter(name: "CIQRCodeGenerator")!
filter.setValue(data, forKey: "inputMessage")
filter.setValue("M", forKey: "inputCorrectionLevel")
let ciImage = filter.outputImage!
let scale = CGFloat(${Math.ceil(size / 23)})
let transformed = ciImage.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
let rep = NSBitmapImageRep(ciImage: transformed)
let pngData = rep.representation(using: .png, properties: [:])!
try! pngData.write(to: URL(fileURLWithPath: "${outPath}"))
print("ok")
\`;
app.doShellScript("echo '" + script.replace(/'/g, "'\\\\''") + "' | swift -");
`;
      await execFileAsync("osascript", ["-l", "JavaScript", "-e", jxaScript], {
        timeout: 15000,
      });

      if (existsSync(outPath)) {
        return (
          `QR code generated: ${outPath}\n` +
          `Data: ${data.length > 100 ? data.slice(0, 100) + "..." : data}\n` +
          `Use send_file to deliver this to the user.`
        );
      }
    } catch {
      // Swift/CoreImage fallback failed
    }

    return (
      `Error: Could not generate QR code. Install a Python QR library:\n` +
      `  pip3 install qrcode[pil]\n` +
      `  — or —\n` +
      `  pip3 install segno`
    );
  },
};
