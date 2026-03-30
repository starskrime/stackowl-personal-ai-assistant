---
name: browser_downloads
description: View, pause, resume, cancel, and clear browser downloads for Safari, Chrome, and Firefox
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "⬇️"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: list, pause, resume, cancel, clear, open_folder"
    default: "list"
  browser:
    type: string
    description: "Browser: safari, chrome, firefox, or all"
    default: "all"
  download_name:
    type: string
    description: "Download file name for targeted operations"
required: []
steps:
  - id: list_safari_downloads
    tool: ShellTool
    args:
      command: "ls -lt ~/Downloads/* 2>/dev/null | head -20 | awk '{print $NF, $5}'"
      mode: "local"
    timeout_ms: 10000
  - id: list_chrome_downloads
    tool: ShellTool
    args:
      command: "sqlite3 ~/Library/Application\ Support/Google/Chrome/Default/History 'SELECT tab_url, tab_display_title FROM downloads' 2>/dev/null | head -20 || echo 'Could not read Chrome downloads'"
      mode: "local"
    timeout_ms: 10000
  - id: open_downloads_folder
    tool: ShellTool
    args:
      command: "open ~/Downloads && echo 'Opened Downloads folder'"
      mode: "local"
    timeout_ms: 5000
  - id: clear_downloads
    tool: ShellTool
    args:
      command: "rm -i ~/Downloads/* 2>/dev/null && echo 'Downloads cleared'"
      mode: "local"
    timeout_ms: 30000
  - id: cancel_chrome_download
    tool: ShellTool
    args:
      command: "echo 'Chrome downloads cannot be cancelled via script - close Chrome to cancel'"
      mode: "local"
    timeout_ms: 3000
  - id: find_recent_downloads
    tool: ShellTool
    args:
      command: "find ~/Downloads -type f -mmin -60 -ls 2>/dev/null | tail -20"
      mode: "local"
    timeout_ms: 10000
  - id: download_size
    tool: ShellTool
    args:
      command: "du -sh ~/Downloads 2>/dev/null"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "Browser downloads status:\n\nRecent downloads:\n{{find_recent_downloads.output}}\n\nTotal Downloads size: {{download_size.output}}"
    depends_on: [find_recent_downloads]
    inputs: [find_recent_downloads.output, download_size.output]
---

# Browser Downloads

Manage browser downloads.

## Usage

List recent downloads:
```
/browser_downloads
```

Open Downloads folder:
```
action=open_folder
```

Clear download history:
```
action=clear
```

## Actions

- **list**: Show recent downloads
- **pause**: Pause download (Chrome)
- **resume**: Resume download (Chrome)
- **cancel**: Cancel download
- **clear**: Clear Downloads folder
- **open_folder**: Open Downloads folder

## Examples

### List all downloads
```
action=list
browser=all
```

### Open Downloads
```
action=open_folder
```

### Clear old downloads
```
action=clear
```

## Notes

- Downloads typically go to ~/Downloads
- Chrome/Firefox have internal download managers
- Cancel in Chrome requires closing the browser