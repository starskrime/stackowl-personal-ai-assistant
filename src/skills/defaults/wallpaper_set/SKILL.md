---
name: wallpaper_set
description: Set the macOS desktop wallpaper from a local file path or downloaded image URL
openclaw:
  emoji: "🖥️"
  os: [darwin]
---
# Set Desktop Wallpaper
Change macOS desktop wallpaper.
## Steps
1. **If URL provided, download first:**
   ```bash
   run_shell_command("curl -s -o /tmp/wallpaper.jpg '<image_url>'")
   ```
2. **Set wallpaper:**
   ```bash
   run_shell_command("osascript -e 'tell application \"Finder\" to set desktop picture to POSIX file \"<image_path>\"'")
   ```
3. **Confirm** the wallpaper was changed.
## Examples
### Set from local file
```bash
run_shell_command("osascript -e 'tell application \"Finder\" to set desktop picture to POSIX file \"/Users/user/Pictures/wallpaper.jpg\"'")
```
## Error Handling
- **File not found:** Check path or download from URL.
- **Unsupported format:** Convert to JPEG first using `sips`.
