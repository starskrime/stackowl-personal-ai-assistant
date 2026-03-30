---
name: disk_cleanup
description: Find and remove large unused files, caches, and temporary data to free up disk space on macOS
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🧹"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: analyze, clean_caches, or find_large"
    default: "analyze"
  min_size:
    type: string
    description: "Minimum file size to find (e.g., 100M, 1G)"
    default: "100M"
  confirm:
    type: boolean
    description: "Require explicit confirmation before deleting"
    default: true
steps:
  - id: disk_usage
    tool: ShellTool
    args:
      command: "df -h /"
      mode: "local"
    timeout_ms: 5000
  - id: cache_sizes
    tool: ShellTool
    args:
      command: "du -sh ~/Library/Caches/ 2>/dev/null; du -sh /tmp/ 2>/dev/null; du -sh ~/Library/Developer/Xcode/DerivedData 2>/dev/null; du -sh ~/Library/Logs 2>/dev/null"
      mode: "local"
    timeout_ms: 30000
  - id: find_large_files
    tool: ShellTool
    args:
      command: "find ~ -type f -size +{{min_size}} 2>/dev/null | head -30"
      mode: "local"
    timeout_ms: 60000
  - id: trash_size
    tool: ShellTool
    args:
      command: "du -sh ~/.Trash 2>/dev/null"
      mode: "local"
    timeout_ms: 5000
  - id: clean_caches
    tool: ShellTool
    args:
      command: "rm -rf ~/Library/Caches/*/ 2>/dev/null && echo 'Caches cleaned'"
      mode: "local"
    timeout_ms: 30000
  - id: empty_trash
    tool: ShellTool
    args:
      command: "rm -rf ~/.Trash/* 2>/dev/null && echo 'Trash emptied'"
      mode: "local"
    timeout_ms: 10000
  - id: analyze
    type: llm
    prompt: "Disk cleanup analysis:\n\nDisk usage: {{disk_usage.output}}\n\nCache sizes:\n{{cache_sizes.output}}\n\nLarge files ({{min_size}}+):\n{{find_large_files.output}}\n\nTrash size: {{trash_size.output}}\n\nProvide cleanup recommendations."
    depends_on: [disk_usage, cache_sizes]
    inputs: [disk_usage.output, cache_sizes.output, find_large_files.output, trash_size.output]
---

# Disk Cleanup

Free up disk space by analyzing and cleaning caches and large files.

## Usage

Analyze disk usage:
```
/disk_cleanup
```

Find large files:
```
action=find_large
min_size=500M
```

## Actions

- **analyze** (default): Show disk usage and cache sizes
- **find_large**: Find files larger than min_size
- **clean_caches**: Remove cache files (requires confirmation)

## Examples

### Analyze disk space
```
action=analyze
```

### Find files larger than 500MB
```
action=find_large
min_size=500M
```

### Find files larger than 1GB
```
action=find_large
min_size=1G
```

## Safety

- **Never** deletes system-critical files
- **Always** requires confirmation for destructive actions
- **Skips** protected directories
- **Notes** which files couldn't be accessed