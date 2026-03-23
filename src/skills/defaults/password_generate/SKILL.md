---
name: password_generate
description: Generate cryptographically secure random passwords with configurable length and character requirements
openclaw:
  emoji: "🔐"
---
# Password Generator
Generate secure random passwords.
## Steps
1. **Determine requirements:** length (default 20), include uppercase, lowercase, digits, symbols.
2. **Generate using OpenSSL:**
   ```bash
   run_shell_command("openssl rand -base64 24 | head -c <length>")
   ```
   Or for customizable charset:
   ```bash
   run_shell_command("LC_ALL=C tr -dc 'A-Za-z0-9!@#$%^&*' < /dev/urandom | head -c <length>")
   ```
3. **Copy to clipboard:**
   ```bash
   run_shell_command("echo -n '<password>' | pbcopy")
   ```
4. **Confirm** password was generated and copied.
## Examples
### Generate 24-char password
```bash
run_shell_command("LC_ALL=C tr -dc 'A-Za-z0-9!@#$%^&*' < /dev/urandom | head -c 24")
```
## Error Handling
- **Clipboard not available:** Display the password directly (warn about screen visibility).
