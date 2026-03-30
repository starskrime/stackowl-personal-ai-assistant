---
name: archive_create
description: Create compressed archives in zip, tar.gz, or tar.bz2 format from files or directories
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "📦"
parameters:
  source:
    type: string
    description: "Source file or directory to archive"
  output:
    type: string
    description: "Output archive path (without extension)"
    default: "archive"
  format:
    type: string
    description: "Archive format: zip, tar.gz, or tar.bz2"
    default: "tar.gz"
required: [source]
steps:
  - id: check_source
    tool: ShellTool
    args:
      command: "ls -la {{source}} 2>/dev/null || echo 'Source not found'"
      mode: "local"
    timeout_ms: 5000
  - id: check_disk_space
    tool: ShellTool
    args:
      command: "df -h . | tail -1"
      mode: "local"
    timeout_ms: 3000
  - id: create_zip
    tool: ShellTool
    args:
      command: "zip -r {{output}}.zip {{source}}"
      mode: "local"
    timeout_ms: 60000
    optional: true
  - id: create_tar_gz
    tool: ShellTool
    args:
      command: "tar -czf {{output}}.tar.gz {{source}}"
      mode: "local"
    timeout_ms: 60000
    optional: true
  - id: create_tar_bz2
    tool: ShellTool
    args:
      command: "tar -cjf {{output}}.tar.bz2 {{source}}"
      mode: "local"
    timeout_ms: 60000
    optional: true
  - id: verify_archive
    tool: ShellTool
    args:
      command: "ls -lh {{output}}.* 2>/dev/null | tail -5"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "Archive creation result for '{{source}}' as {{format}}:\n\nDisk space: {{check_disk_space.output}}\n\nArchive created: {{verify_archive.output}}\n\nProvide a summary of the archive."
    depends_on: [check_disk_space, verify_archive]
    inputs: [check_disk_space.output, verify_archive.output]
---

# Create Archive

Create compressed archives in various formats.

## Usage

```bash
/archive_create ./my_folder
```

With options:
```
source=./my_folder
output=~/backup
format=tar.gz
```

## Formats

- **tar.gz** (default) - Best for Linux/macOS compatibility
- **zip** - Best for cross-platform sharing
- **tar.bz2** - Better compression but slower

## Examples

### Zip archive
```
source=./src
output=project
format=zip
```

### tar.gz archive
```
source=~/Documents
output=docs_backup
format=tar.gz
```

## Error Handling

- **Source not found:** Verifies path exists first
- **Disk full:** Checks available space before creating
- **Permission denied:** Reports access issues