---
name: qr_code_generate
description: Generate QR codes from text, URLs, or contact information and save as image files
openclaw:
  emoji: "◻️"
---

# QR Code Generator

Generate QR codes from text or URLs.

## Steps

1. **Get content** to encode (URL, text, vCard, WiFi config).
2. **Generate using Python:**
   ```bash
   run_shell_command("python3 -c \"import qrcode; img=qrcode.make('<content>'); img.save('/tmp/qrcode.png')\"")
   ```
   If qrcode module unavailable, use web API:
   ```bash
   run_shell_command("curl -o /tmp/qrcode.png 'https://api.qrserver.com/v1/create-qr-code/?size=300x300&data=<encoded_content>'")
   ```
3. **Send the image:**
   ```yaml
   send_file:
     path: "/tmp/qrcode.png"
     caption: "QR Code for: <content>"
   ```

## Examples

### Generate URL QR code

```bash
run_shell_command("curl -o /tmp/qr.png 'https://api.qrserver.com/v1/create-qr-code/?size=300x300&data=https://example.com'")
```

## Error Handling

- **Content too long:** QR codes have data limits; warn if exceeding ~4000 chars.
- **No Python qrcode module:** Use web API fallback.
