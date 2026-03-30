---
name: dns_lookup
description: Perform DNS lookups to resolve domain names, check MX records, NS records, and TXT records
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🌐"
parameters:
  domain:
    type: string
    description: "Domain to lookup"
  record_type:
    type: string
    description: "Record type: A, AAAA, MX, NS, TXT, CNAME, ANY"
    default: "A"
steps:
  - id: check_dig
    tool: ShellTool
    args:
      command: "which dig || echo 'NOT_FOUND'"
      mode: "local"
    timeout_ms: 3000
  - id: dns_a
    tool: ShellTool
    args:
      command: "dig +short {{domain}} A +timeout=10"
      mode: "local"
    timeout_ms: 15000
  - id: dns_aaaa
    tool: ShellTool
    args:
      command: "dig +short {{domain}} AAAA +timeout=10"
      mode: "local"
    timeout_ms: 15000
    optional: true
  - id: dns_mx
    tool: ShellTool
    args:
      command: "dig +short {{domain}} MX +timeout=10"
      mode: "local"
    timeout_ms: 15000
    optional: true
  - id: dns_ns
    tool: ShellTool
    args:
      command: "dig +short {{domain}} NS +timeout=10"
      mode: "local"
    timeout_ms: 15000
    optional: true
  - id: dns_txt
    tool: ShellTool
    args:
      command: "dig +short {{domain}} TXT +timeout=10"
      mode: "local"
    timeout_ms: 15000
    optional: true
  - id: dns_cname
    tool: ShellTool
    args:
      command: "dig +short {{domain}} CNAME +timeout=10"
      mode: "local"
    timeout_ms: 15000
    optional: true
  - id: dns_any
    tool: ShellTool
    args:
      command: "dig {{domain}} ANY +noall +answer +timeout=10"
      mode: "local"
    timeout_ms: 15000
    optional: true
  - id: fallback_nslookup
    tool: ShellTool
    args:
      command: "nslookup {{domain}} 2>/dev/null | head -20"
      mode: "local"
    timeout_ms: 10000
  - id: analyze
    type: llm
    prompt: "DNS lookup for '{{domain}}' (type: {{record_type}}):\n\n{{#if_eq record_type 'A'}}A records:\n{{dns_a.output}}{{/if_eq}}\n{{#if_eq record_type 'MX'}}MX records:\n{{dns_mx.output}}{{/if_eq}}\n{{#if_eq record_type 'NS'}}NS records:\n{{dns_ns.output}}{{/if_eq}}\n{{#if_eq record_type 'TXT'}}TXT records:\n{{dns_txt.output}}{{/if_eq}}\n{{#if_eq record_type 'CNAME'}}CNAME:\n{{dns_cname.output}}{{/if_eq}}\n{{#if_eq record_type 'ANY'}}All records:\n{{dns_any.output}}{{/if_eq}}\n\nProvide a clear summary of DNS records."
    depends_on: [dns_a]
    inputs: [dns_a.output, dns_aaaa.output, dns_mx.output, dns_ns.output, dns_txt.output, dns_cname.output]
---

# DNS Lookup

Perform DNS record lookups for domain names.

## Usage

```bash
/dns_lookup example.com
```

With record type:
```
domain=example.com
record_type=MX
```

## Record Types

- **A**: IPv4 address
- **AAAA**: IPv6 address
- **MX**: Mail exchange servers
- **NS**: Nameservers
- **TXT**: Text records
- **CNAME**: Canonical name
- **ANY**: All available records

## Examples

### Get IP address
```
domain=example.com
record_type=A
```

### Get mail servers
```
domain=example.com
record_type=MX
```

### Get nameservers
```
domain=example.com
record_type=NS
```

### Get all records
```
domain=example.com
record_type=ANY
```

## Error Handling

- **dig not available:** Falls back to nslookup
- **Domain not found:** Reports NXDOMAIN
- **Timeout:** Uses 10 second timeout per query