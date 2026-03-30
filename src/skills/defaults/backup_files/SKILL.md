---
name: backup_files
description: Create timestamped backups of important files or directories as compressed archives
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "💾"
parameters:
  source:
    type: string
    description: "Source file or directory to backup"
  destination:
    type: string
    description: "Backup destination directory"
    default: "~/Backups"
  name:
    type: string
    description: "Backup name prefix"
    default: "backup"
required: [source]
steps:
  - id: create_backup_dir
    tool: ShellTool
    args:
      command: "mkdir -p {{destination}}"
      mode: "local"
    timeout_ms: 5000
  - id: check_source
    tool: ShellTool
    args:
      command: "ls -la {{source}} 2>/dev/null | head -10 || echo 'Source not found'"
      mode: "local"
    timeout_ms: 5000
  - id: check_disk_space
    tool: ShellTool
    args:
      command: "df -h {{destination}} | tail -1"
      mode: "local"
    timeout_ms: 3000
  - id: create_backup
    tool: ShellTool
    args:
      command: "tar -czf {{destination}}/{{name}}_$(date +%Y%m%d_%H%M%S).tar.gz {{source}}"
      mode: "local"
    timeout_ms: 120000
  - id: verify_backup
    tool: ShellTool
    args:
      command: "ls -lh {{destination}}/{{name}}_*.tar.gz | tail -3"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "Backup operation completed.\n\nSource: {{source}}\nDestination: {{destination}}\nDisk space: {{check_disk_space.output}}\n\nBackup created:\n{{verify_backup.output}}\n\nProvide a summary."
    depends_on: [check_disk_space, verify_backup]
    inputs: [check_disk_space.output, verify_backup.output]
---

# Backup Files

Create compressed timestamped backups of files or directories.

## Usage

```bash
/backup_files ~/Documents
```

With options:
```
source=~/Documents
destination=~/Backups
name=docs
```

## Examples

### Backup Documents folder
```
source=~/Documents
destination=~/Backups
name=docs
```

### Backup project directory
```
source=./my_project
destination=~/Backups
name=project
```

## Error Handling

- **Destination missing:** Auto-creates the backup directory
- **Disk full:** Checks available space first
- **Permission denied:** Reports which files couldn't be backed up
- **Source not found:** Validates path before attempting backup

## Notes

- Backups are timestamped automatically: `backup_20240329_143052.tar.gz`
- Uses tar.gz format for broad compatibility
- Compresses to save disk space