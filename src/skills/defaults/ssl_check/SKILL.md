---
name: ssl_check
description: Verify SSL/TLS certificate validity, expiration date, and chain for a given domain
openclaw:
  emoji: "🔒"
---

# SSL Certificate Check

Verify SSL certificates for domains.

## Steps

1. **Check certificate details:**
   ```bash
   run_shell_command("echo | openssl s_client -connect <domain>:443 -servername <domain> 2>/dev/null | openssl x509 -noout -dates -subject -issuer")
   ```
2. **Check expiration:**
   ```bash
   run_shell_command("echo | openssl s_client -connect <domain>:443 2>/dev/null | openssl x509 -noout -enddate")
   ```
3. **Present:** issuer, subject, valid from/to, days until expiration.

## Examples

### Check google.com

```bash
run_shell_command("echo | openssl s_client -connect google.com:443 2>/dev/null | openssl x509 -noout -dates")
```

## Error Handling

- **Connection refused:** Port 443 may not be open; check if HTTPS is served.
- **Self-signed cert:** Note the warning but still show details.
