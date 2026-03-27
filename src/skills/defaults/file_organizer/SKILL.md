---
name: file_organizer
description: Organize files in a directory by sorting them into subfolders based on file type, date, or custom rules
openclaw:
  emoji: "📂"
---

# File Organizer

Sort files into subfolders by type or date.

## Steps

1. **List files in target directory:**
   ```bash
   run_shell_command("ls -la <directory>")
   ```
2. **Create category folders:**
   ```bash
   run_shell_command("mkdir -p <directory>/{Images,Documents,Videos,Audio,Archives,Code,Other}")
   ```
3. **Move files by extension:**
   ```bash
   run_shell_command("mv <directory>/*.{jpg,jpeg,png,gif,svg} <directory>/Images/ 2>/dev/null")
   run_shell_command("mv <directory>/*.{pdf,doc,docx,txt,md} <directory>/Documents/ 2>/dev/null")
   run_shell_command("mv <directory>/*.{mp4,mov,avi,mkv} <directory>/Videos/ 2>/dev/null")
   ```
4. **Show summary** of files moved per category.

## Examples

### Organize Downloads folder

```bash
run_shell_command("mkdir -p ~/Downloads/{Images,Documents,Videos} && mv ~/Downloads/*.pdf ~/Downloads/Documents/ 2>/dev/null")
```

## Error Handling

- **Empty directory:** Inform user "No files to organize."
- **Permission denied:** Skip protected files and report them.
- **Name conflicts:** Append timestamp to avoid overwriting.
