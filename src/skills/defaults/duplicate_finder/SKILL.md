---
name: duplicate_finder
description: Find duplicate files in a directory by comparing file sizes and checksums
openclaw:
  emoji: "👯"
---

# Duplicate Finder

Find duplicate files using checksums.

## Steps

1. **Find files with duplicate sizes (quick pre-filter):**
   ```bash
   run_shell_command("find <directory> -type f -exec stat -f '%z %N' {} + | sort -n | uniq -d -w 10")
   ```
2. **Compare checksums for same-size files:**
   ```bash
   run_shell_command("find <directory> -type f -exec md5 {} + | sort | awk -F'=' '{print $2}' | sort | uniq -d")
   ```
3. **Present duplicates** grouped by content with file sizes.
4. **Ask user** which copies to keep or delete.

## Examples

### Find duplicates in Downloads

```bash
run_shell_command("find ~/Downloads -type f -exec md5 -r {} + | sort | uniq -d -w 32")
```

## Error Handling

- **Large directory:** Limit depth with `-maxdepth 3` to avoid long scans.
- **Permission denied:** Skip inaccessible files.
