---
name: clipboard_advanced
description: Advanced clipboard operations - copy files, images, formats, clipboard history and management on macOS
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "📎"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: copy, paste, history, clear, or info"
    default: "read"
  content:
    type: string
    description: "Content to copy to clipboard"
  path:
    type: string
    description: "File path to copy"
  format:
    type: string
    description: "Format type: text, image, rtfd, files"
    default: "text"
required: []
steps:
  - id: read_clipboard
    tool: ShellTool
    args:
      command: "pbpaste"
      mode: "local"
    timeout_ms: 5000
  - id: copy_text
    tool: ShellTool
    args:
      command: "echo '{{content}}' | pbcopy && echo 'Copied to clipboard'"
      mode: "local"
    timeout_ms: 5000
  - id: copy_file
    tool: ShellTool
    args:
      command: "echo -n 'file://{{path}}' | pbcopy && echo 'File reference copied'"
      mode: "local"
    timeout_ms: 5000
  - id: copy_image
    tool: ShellTool
    args:
      command: "osascript -e 'set the clipboard to (read (POSIX file \"{{path}}\") as JPEG picture)' && echo 'Image copied'"
      mode: "local"
    timeout_ms: 10000
  - id: clipboard_info
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to return clipboard info' 2>/dev/null | head -20"
      mode: "local"
    timeout_ms: 5000
  - id: clipboard_history
    tool: ShellTool
    args:
      command: "ls -lt ~/Library/Containers/com.apple.pasteboard.clipboard*/Data 2>/dev/null | head -10 || echo 'History not accessible'"
      mode: "local"
    timeout_ms: 10000
  - id: clear_clipboard
    tool: ShellTool
    args:
      command: "pbcopy < /dev/null && echo 'Clipboard cleared'"
      mode: "local"
    timeout_ms: 3000
  - id: analyze
    type: llm
    prompt: "Clipboard action: '{{action}}'\n\nClipboard content preview:\n{{read_clipboard.output}}\n\nInfo:\n{{clipboard_info.output}}"
    depends_on: [read_clipboard]
    inputs: [read_clipboard.output, clipboard_info.output]
---

# Clipboard Advanced

Advanced clipboard operations on macOS.

## Usage

Read clipboard:
```
/clipboard_advanced
```

Copy text:
```
action=copy
content=Hello World
```

Copy file reference:
```
action=copy_file
path=/Users/name/Documents/file.pdf
```

Copy image:
```
action=copy_image
path=/Users/name/Pictures/screenshot.png
```

Get clipboard info:
```
action=info
```

Clear clipboard:
```
action=clear
```

## Actions

- **read** (default): Show clipboard contents
- **copy**: Copy text to clipboard
- **copy_file**: Copy file as file reference
- **copy_image**: Copy image to clipboard
- **info**: Get clipboard data types
- **history**: Show recent clipboard items
- **clear**: Clear clipboard contents

## Examples

### Copy text
```
action=copy
content=Some important text
```

### Copy image
```
action=copy_image
path=~/Desktop/photo.jpg
```

### Check what's on clipboard
```
action=info
```

## Supported Formats

- **text**: Plain text (default)
- **rtfd**: Rich text with formatting
- **image**: JPEG, PNG, TIFF
- **files**: File references

## Notes

- Binary data shows as `<binary>`
- Large clipboard may take time to read
- Some apps have custom clipboard formats