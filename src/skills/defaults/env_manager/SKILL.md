---
name: env_manager
description: Manage environment variables by viewing, setting, and creating .env files for projects
openclaw:
  emoji: "🌍"
---
# Environment Variable Manager
Manage env vars and .env files.
## Steps
1. **View current env vars:**
   ```bash
   run_shell_command("env | sort | head -30")
   ```
2. **Search for specific var:**
   ```bash
   run_shell_command("echo $<VAR_NAME>")
   ```
3. **Read .env file:**
   ```bash
   read_file(".env")
   ```
4. **Create/update .env:**
   ```bash
   write_file(".env", "<KEY>=<value>\n<KEY2>=<value2>")
   ```
## Examples
### Check API key is set
```bash
run_shell_command("echo $OPENAI_API_KEY | head -c 10")
```
## Error Handling
- **Sensitive values:** Never display full API keys — show only first/last 4 chars.
- **.env not in .gitignore:** Warn user to add it.
