---
name: ssl_check
description: Verify SSL/TLS certificate validity, expiration date, and chain for a given domain
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🔒"
parameters:
  domain:
    type: string
    description: "Domain to check SSL certificate for"
required: [domain]
steps:
  - id: check_certificate
    tool: ShellTool
    args:
      command: "echo | openssl s_client -connect {{domain}}:443 -servername {{domain}} 2>/dev/null | openssl x509 -noout -dates -subject -issuer"
      mode: "local"
    timeout_ms: 15000
  - id: check_expiration
    tool: ShellTool
    args:
      command: "echo | openssl s_client -connect {{domain}}:443 2>/dev/null | openssl x509 -noout -enddate"
      mode: "local"
    timeout_ms: 15000
  - id: present_results
    type: llm
    prompt: "Present the SSL certificate details for {{domain}}:\n\nCertificate details:\n{{check_certificate.output}}\n\nExpiration:\n{{check_expiration.output}}\n\nFormat as:\n- Issuer\n- Subject\n- Valid From\n- Valid Until\n- Days Until Expiration (calculate if possible)"
    depends_on: [check_certificate, check_expiration]
    inputs: [check_certificate.output, check_expiration.output]
---

# SSL Certificate Check

Verify SSL certificates for domains.

## Usage

```bash
/ssl_check domain="google.com"
```

## Parameters

- **domain**: Domain to check SSL certificate for

## Examples

```
ssl_check domain="google.com"
ssl_check domain="github.com"
```

## Error Handling

- **Connection refused:** Port 443 may not be open; check if HTTPS is served.
- **Self-signed cert:** Note the warning but still show details.
