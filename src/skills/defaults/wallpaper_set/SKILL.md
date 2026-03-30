---
name: wallpaper_set
description: Set the macOS desktop wallpaper from a local file path or downloaded image URL
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🖥️"
  os: [darwin]
parameters:
  image_path:
    type: string
    description: "Local path to image file"
  image_url:
    type: string
    description: "URL to download image from (optional if image_path provided)"
required: [image_path]
steps:
  - id: download_image
    tool: ShellTool
    args:
      command: "curl -s -o /tmp/wallpaper.jpg '{{image_url}}'"
      mode: "local"
    timeout_ms: 30000
    optional: true
  - id: set_wallpaper
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Finder\" to set desktop picture to POSIX file \"{{image_path}}\"'"
      mode: "local"
    timeout_ms: 10000
  - id: confirm
    type: llm
    prompt: "Confirm whether the wallpaper was successfully set to {{image_path}}. If there was an error, suggest fixes."
    depends_on: [set_wallpaper]
    inputs: [set_wallpaper.output]
---

# Set Desktop Wallpaper

Change macOS desktop wallpaper.

## Usage

```bash
/wallpaper_set image_path="/Users/user/Pictures/wallpaper.jpg"
```

## Parameters

- **image_path**: Local path to image file
- **image_url**: URL to download image from (optional if image_path provided)

## Error Handling

- **File not found:** Check path or download from URL.
- **Unsupported format:** Convert to JPEG first using `sips`.
