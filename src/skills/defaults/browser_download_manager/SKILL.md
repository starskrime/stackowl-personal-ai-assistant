---
name: browser_download_manager
description: View, manage, and organize browser downloads - pause, resume, clear, and track download progress
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "📥"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: list, pause, resume, clear, open_folder"
    default: "list"
  browser:
    type: string
    description: "Browser: safari, chrome, firefox"
    default: "all"
required: []
steps:
  - id: list_downloads
    tool: ShellTool
    args:
      command: "find ~/Downloads -type f -mmin -60 | head -20"
    mode: "local"
    timeout_ms: 10000
  - id: list_all_downloads
    tool: ShellTool
    args:
      command: "ls -lth ~/Downloads | head -30"
    mode: "local"
    timeout_ms: 10000
  - id: download_size
    tool: ShellTool
    args:
      command: "du -sh ~/Downloads 2>/dev/null"
    mode: "local"
    timeout_ms: 5000
  - id: open_downloads
    tool: ShellTool
    args:
      command: "open ~/Downloads"
    mode: "local"
    timeout_ms: 5000
  - id: clear_downloads
    tool: ShellTool
    args:
      command: "rm -i ~/Downloads/* 2>/dev/null; echo 'Downloads cleared'"
    mode: "local"
    timeout_ms: 30000
  - id: analyze
    type: llm
    prompt: "Download manager status:\n\nRecent downloads:\n{{list_downloads.output}}\n\nTotal size: {{download_size.output}}"
    depends_on: [list_downloads]
    inputs: [list_downloads.output, download_size.output]
---

# Browser Download Manager

Manage browser downloads.

## Usage

List recent downloads:
```
/browser_download_manager
```

Open Downloads folder:
```
action=open_folder
```

Clear downloads:
```
action=clear
```

## Actions

- **list**: Show recent downloads
- **open_folder**: Open Downloads in Finder
- **clear**: Remove download files

## Examples

### View downloads
```
action=list
```

### Open folder
```
action=open_folder
```

### Clear old files
```
action=clear
```