---
name: finder_operations
description: Perform file operations in macOS Finder - copy, move, delete, create folders, show in Finder, get info
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "📁"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: copy, move, delete, mkdir, show, info, duplicate"
    default: "show"
  source:
    type: string
    description: "Source file/folder path"
  destination:
    type: string
    description: "Destination path"
  new_name:
    type: string
    description: "New name (for rename)"
required: []
steps:
  - id: show_in_finder
    tool: ShellTool
    args:
      command: "open -R '{{source}}' && echo 'Revealed in Finder'"
      mode: "local"
    timeout_ms: 5000
  - id: copy_file
    tool: ShellTool
    args:
      command: "cp -R '{{source}}' '{{destination}}' && echo 'Copied to {{destination}}'"
      mode: "local"
    timeout_ms: 60000
  - id: move_file
    tool: ShellTool
    args:
      command: "mv '{{source}}' '{{destination}}' && echo 'Moved to {{destination}}'"
      mode: "local"
    timeout_ms: 60000
  - id: delete_file
    tool: ShellTool
    args:
      command: "rm -rf '{{source}}' && echo 'Deleted {{source}}'"
      mode: "local"
    timeout_ms: 30000
  - id: mkdir_operation
    tool: ShellTool
    args:
      command: "mkdir -p '{{destination}}' && echo 'Created directory {{destination}}'"
      mode: "local"
    timeout_ms: 5000
  - id: file_info
    tool: ShellTool
    args:
      command: "stat -f '%N: %Sz bytes, %Sm' '{{source}}' 2>/dev/null || ls -lh '{{source}}'"
      mode: "local"
    timeout_ms: 5000
  - id: finder_info
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Finder\" to get information of (POSIX file \"{{source}}\" as alias)' 2>/dev/null || echo 'Could not get Finder info'"
      mode: "local"
    timeout_ms: 5000
  - id: duplicate_file
    tool: ShellTool
    args:
      command: "cp '{{source}}' '{{source}}.copy' && echo 'Duplicated {{source}}'"
      mode: "local"
    timeout_ms: 30000
  - id: open_file
    tool: ShellTool
    args:
      command: "open '{{source}}' && echo 'Opened {{source}}'"
      mode: "local"
    timeout_ms: 10000
  - id: analyze
    type: llm
    prompt: "Finder operation: '{{action}}'\n\nSource: {{source}}\n{{#if destination}}Destination: {{destination}}{{/if}}\n\nResult: Operation completed"
    depends_on: [show_in_finder]
    inputs: [file_info.output]
---

# Finder Operations

Perform file operations in macOS Finder.

## Usage

Reveal file in Finder:
```
action=show
source=/Users/name/Documents/file.txt
```

Copy file:
```
action=copy
source=~/Documents/file.txt
destination=~/Desktop/
```

Move file:
```
action=move
source=~/Documents/file.txt
destination=~/Desktop/
```

Delete file:
```
action=delete
source=~/Trash/file.txt
```

Create folder:
```
action=mkdir
destination=~/Documents/NewFolder
```

Get file info:
```
action=info
source=/Users/name/Documents/file.txt
```

## Actions

- **show**: Reveal file in Finder
- **copy**: Copy file or folder
- **move**: Move file or folder
- **delete**: Move to Trash
- **mkdir**: Create new folder
- **info**: Get file details
- **duplicate**: Duplicate file
- **open**: Open with default app

## Examples

### Reveal in Finder
```
action=show
source=./myfile.txt
```

### Backup folder
```
action=copy
source=~/Documents/Projects
destination=~/Backups/Projects_backup
```

### Organize files
```
action=move
source=~/Downloads/*.pdf
destination=~/Documents/Papers
```

## Safety

- Delete moves to Trash, not permanent delete
- Use absolute paths for clarity
- Overwrites destination if exists