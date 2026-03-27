---
name: archive_create
description: Create compressed archives in zip, tar.gz, or tar.bz2 format from files or directories
openclaw:
  emoji: "📦"
---

# Create Archive

Create compressed archives.

## Steps

1. **Choose format** based on user preference:
   - **zip:** `run_shell_command("zip -r <output.zip> <source>")`
   - **tar.gz:** `run_shell_command("tar -czf <output.tar.gz> <source>")`
   - **tar.bz2:** `run_shell_command("tar -cjf <output.tar.bz2> <source>")`
2. **Show result** with file size.

## Examples

### Create zip archive

```bash
run_shell_command("zip -r project.zip ./src/ ./docs/")
```

## Error Handling

- **Source not found:** Check path and suggest corrections.
- **Disk full:** Check space first with `df -h`.
