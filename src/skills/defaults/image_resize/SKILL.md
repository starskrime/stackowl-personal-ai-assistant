---
name: image_resize
description: Resize, compress, or convert images using macOS built-in tools or ImageMagick
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🖼️"
  os: [darwin]
parameters:
  input:
    type: string
    description: "Input image file"
  output:
    type: string
    description: "Output image file"
  width:
    type: number
    description: "Target width in pixels"
  height:
    type: number
    description: "Target height in pixels (optional, auto if width set)"
  format:
    type: string
    description: "Output format: jpg, png, tiff, gif"
    default: "jpg"
required: [input, output]
steps:
  - id: check_tools
    tool: ShellTool
    args:
      command: "which sips && which convert || echo 'sips found'"
      mode: "local"
    timeout_ms: 5000
  - id: get_image_info
    tool: ShellTool
    args:
      command: "sips -g pixelWidth -g pixelHeight {{input}} 2>/dev/null | grep -E 'pixelWidth|pixelHeight'"
      mode: "local"
    timeout_ms: 5000
  - id: resize_with_sips
    tool: ShellTool
    args:
      command: "sips --resampleWidth {{width}} {{input}} --out {{output}}"
      mode: "local"
    timeout_ms: 15000
    optional: true
  - id: resize_with_imagemagick
    tool: ShellTool
    args:
      command: "convert {{input}} -resize {{width}}x{{height}} {{output}}"
      mode: "local"
    timeout_ms: 15000
    optional: true
  - id: convert_format
    tool: ShellTool
    args:
      command: "sips -s format {{format}} {{input}} --out {{output}}"
      mode: "local"
    timeout_ms: 15000
  - id: verify_output
    tool: ShellTool
    args:
      command: "sips -g pixelWidth -g pixelHeight {{output}} 2>/dev/null | grep -E 'pixelWidth|pixelHeight' && ls -lh {{output}}"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "Image resize completed:\n\nInput: {{input}}\nOutput: {{output}}\n\nOriginal info:\n{{get_image_info.output}}\n\nNew size:\n{{verify_output.output}}"
    depends_on: [get_image_info]
    inputs: [get_image_info.output, verify_output.output]
---

# Image Resize

Resize and convert images using macOS built-in tools.

## Usage

```bash
/image_resize input.jpg output_small.jpg
```

Resize to 800px width:
```
input=photo.jpg
output=photo_small.jpg
width=800
```

## Parameters

- **input**: Source image file
- **output**: Destination file
- **width**: Target width (maintains aspect ratio)
- **height**: Target height (optional)
- **format**: Output format (jpg, png, tiff, gif)

## Examples

### Resize to 800px width
```
input=photo.jpg
output=photo_small.jpg
width=800
```

### Convert PNG to JPG
```
input=image.png
output=image.jpg
format=jpg
```

### Resize and convert
```
input=photo.png
output=photo.jpg
width=1024
format=jpg
```

## Tools

- **sips**: macOS built-in, fast
- **convert**: ImageMagick (if sips unavailable)