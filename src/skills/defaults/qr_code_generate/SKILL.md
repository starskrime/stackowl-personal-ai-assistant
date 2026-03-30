---
name: qr_code_generate
description: Generate QR codes from text, URLs, or contact information and save as image files
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "◻️"
parameters:
  content:
    type: string
    description: "The content to encode (URL, text, vCard, WiFi config)"
  output_path:
    type: string
    description: "Output file path for the QR code image"
    default: "/tmp/qrcode.png"
  use_api:
    type: boolean
    description: "Use web API fallback instead of Python qrcode module"
    default: false
required: [content]
steps:
  - id: check_python_qrcode
    tool: ShellTool
    args:
      command: "python3 -c \"import qrcode\" 2>/dev/null && echo 'AVAILABLE' || echo 'NOT_AVAILABLE'"
      mode: "local"
  - id: generate_python
    tool: ShellTool
    args:
      command: "python3 -c \"import qrcode; img=qrcode.make('{{content}}'); img.save('{{output_path}}')\""
      mode: "local"
    optional: true
    depends_on: [check_python_qrcode]
  - id: generate_api
    tool: ShellTool
    args:
      command: "curl -s -o {{output_path}} 'https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={{content}}'"
      mode: "local"
    optional: true
    depends_on: [check_python_qrcode]
  - id: verify_output
    tool: ShellTool
    args:
      command: "ls -la {{output_path}}"
      mode: "local"
    depends_on: [generate_python, generate_api]
---

# QR Code Generator

Generate QR codes from text or URLs.

## Usage

```bash
/qr_code_generate content="https://example.com"
/qr_code_generate content="WIFI:T:WPA;S:MyNetwork;P:password;;" output_path=/tmp/wifi-qr.png
```

## Parameters

- **content**: The content to encode (URL, text, vCard, WiFi config) (required)
- **output_path**: Output file path for the QR code image (default: /tmp/qrcode.png)
- **use_api**: Use web API fallback instead of Python qrcode module (default: false)

## Examples

### Generate URL QR code

```bash
curl -o /tmp/qr.png 'https://api.qrserver.com/v1/create-qr-code/?size=300x300&data=https://example.com'
```

## Error Handling

- **Content too long:** QR codes have data limits; warn if exceeding ~4000 chars.
- **No Python qrcode module:** Use web API fallback.
