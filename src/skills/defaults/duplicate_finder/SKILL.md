---
name: duplicate_finder
description: Find duplicate files in a directory by comparing file sizes and checksums
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "👯"
parameters:
  directory:
    type: string
    description: "Directory to search for duplicates"
    default: "~/Downloads"
  max_depth:
    type: number
    description: "Maximum directory depth to search"
    default: 3
required: []
steps:
  - id: find_duplicates_by_size
    tool: ShellTool
    args:
      command: "find {{directory}} -type f -maxdepth {{max_depth}} -exec stat -f '%z %N' {} + 2>/dev/null | sort -n | uniq -d -w 10 || echo 'No duplicates found by size'"
      mode: "local"
    timeout_ms: 60000
  - id: find_duplicates_by_hash
    tool: ShellTool
    args:
      command: "find {{directory}} -type f -maxdepth {{max_depth}} -exec md5 -r {} + 2>/dev/null | sort | awk '{print $1}' | uniq -d | while read hash; do find {{directory}} -type f -maxdepth {{max_depth}} -exec md5 -r {} + 2>/dev/null | grep \"^$hash\"; done || echo 'No duplicates found by hash'"
      mode: "local"
    timeout_ms: 120000
  - id: present_duplicates
    type: llm
    prompt: "Analyze the duplicate file results and present them grouped by content. Show file paths, sizes, and suggest which copies to keep or delete.\n\nBy size:\n{{find_duplicates_by_size.stdout}}\n\nBy hash:\n{{find_duplicates_by_hash.stdout}}"
    depends_on: [find_duplicates_by_size, find_duplicates_by_hash]
    inputs: [find_duplicates_by_size.stdout, find_duplicates_by_hash.stdout]
---

# Duplicate Finder

Find duplicate files using checksums.

## Steps

1. **Find files with duplicate sizes (quick pre-filter):**
   ```bash
   find <directory> -type f -exec stat -f '%z %N' {} + | sort -n | uniq -d -w 10
   ```
2. **Compare checksums for same-size files:**
   ```bash
   find <directory> -type f -exec md5 {} + | sort | awk -F'=' '{print $2}' | sort | uniq -d
   ```
3. **Present duplicates** grouped by content with file sizes.
4. **Ask user** which copies to keep or delete.

## Examples

### Find duplicates in Downloads

```
directory="~/Downloads"
max_depth=3
```

## Error Handling

- **Large directory:** Limit depth with `-maxdepth 3` to avoid long scans.
- **Permission denied:** Skip inaccessible files.
