---
name: privacy_audit
description: Audit macOS privacy and security settings including firewall, FileVault, SIP, and app permissions
openclaw:
  emoji: "🕵️"
  os: [darwin]
---
# Privacy Audit
Audit macOS privacy/security settings.
## Steps
1. **Check firewall:**
   ```bash
   run_shell_command("sudo /usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate 2>/dev/null || echo 'Requires admin'")
   ```
2. **Check FileVault:**
   ```bash
   run_shell_command("fdesetup status")
   ```
3. **Check SIP:**
   ```bash
   run_shell_command("csrutil status")
   ```
4. **Check Gatekeeper:**
   ```bash
   run_shell_command("spctl --status")
   ```
5. **Present audit report** with recommendations.
## Examples
### Full audit
```bash
run_shell_command("fdesetup status && csrutil status && spctl --status")
```
## Error Handling
- **Requires sudo:** Note which checks need admin privileges.
