---
name: dns_lookup
description: Perform DNS lookups to resolve domain names, check MX records, NS records, and TXT records
openclaw:
  emoji: "🌐"
---

# DNS Lookup

Perform DNS record lookups.

## Steps

1. **A record (IP address):**
   ```bash
   run_shell_command("dig +short <domain> A")
   ```
2. **MX records (mail):**
   ```bash
   run_shell_command("dig +short <domain> MX")
   ```
3. **NS records (nameservers):**
   ```bash
   run_shell_command("dig +short <domain> NS")
   ```
4. **TXT records:**
   ```bash
   run_shell_command("dig +short <domain> TXT")
   ```
5. **CNAME:**
   ```bash
   run_shell_command("dig +short <domain> CNAME")
   ```

## Examples

### Full DNS lookup

```bash
run_shell_command("dig <domain> ANY +noall +answer")
```

## Error Handling

- **Domain not found:** NXDOMAIN response — check spelling.
- **dig not available:** Fall back to `nslookup <domain>`.
