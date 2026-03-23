---
name: hash_file
description: Calculate and verify file checksums using MD5, SHA-256, or SHA-512 hash algorithms
openclaw:
  emoji: "🔏"
---
# File Hash Calculator
Calculate and verify file checksums.
## Steps
1. **Calculate hash:**
   - **SHA-256:** `run_shell_command("shasum -a 256 <file>")`
   - **MD5:** `run_shell_command("md5 <file>")`
   - **SHA-512:** `run_shell_command("shasum -a 512 <file>")`
2. **Verify against expected hash** if provided:
   ```bash
   run_shell_command("echo '<expected_hash>  <file>' | shasum -a 256 -c")
   ```
## Examples
### SHA-256 hash
```bash
run_shell_command("shasum -a 256 downloaded_file.iso")
```
## Error Handling
- **File not found:** Check path and suggest alternatives.
