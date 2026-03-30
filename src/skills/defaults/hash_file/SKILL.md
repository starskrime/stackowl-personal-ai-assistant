---
name: hash_file
description: Calculate and verify file checksums using MD5, SHA-256, or SHA-512 hash algorithms
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🔏"
parameters:
  file:
    type: string
    description: "File to hash"
  algorithm:
    type: string
    description: "Hash algorithm: md5, sha256, sha512"
    default: "sha256"
  expected_hash:
    type: string
    description: "Expected hash to verify against"
required: [file]
steps:
  - id: check_file
    tool: ShellTool
    args:
      command: "ls -la {{file}}"
      mode: "local"
    timeout_ms: 5000
  - id: hash_md5
    tool: ShellTool
    args:
      command: "md5 {{file}}"
      mode: "local"
    timeout_ms: 30000
  - id: hash_sha256
    tool: ShellTool
    args:
      command: "shasum -a 256 {{file}}"
      mode: "local"
    timeout_ms: 30000
  - id: hash_sha512
    tool: ShellTool
    args:
      command: "shasum -a 512 {{file}}"
      mode: "local"
    timeout_ms: 30000
  - id: verify_hash
    tool: ShellTool
    args:
      command: "echo '{{expected_hash}}  {{file}}' | shasum -a 256 -c"
      mode: "local"
    timeout_ms: 10000
  - id: analyze
    type: llm
    prompt: "Hash calculation for: {{file}}\n\nAlgorithm: {{algorithm}}\n\n{{#if_eq algorithm 'md5'}}MD5: {{hash_md5.output}}{{/if_eq}}\n{{#if_eq algorithm 'sha256'}}SHA-256: {{hash_sha256.output}}{{/if_eq}}\n{{#if_eq algorithm 'sha512'}}SHA-512: {{hash_sha512.output}}{{/if_eq}}\n\n{{#if expected_hash}}Verification against expected:\n{{verify_hash.output}}{{/if}}"
    depends_on: [check_file]
    inputs: [hash_md5.output, hash_sha256.output, hash_sha512.output, verify_hash.output]
---

# File Hash Calculator

Calculate and verify file checksums.

## Usage

```bash
/hash_file document.zip
```

With algorithm:
```
file=document.zip
algorithm=sha256
```

Verify against expected hash:
```
file=document.zip
algorithm=sha256
expected_hash=abc123...
```

## Algorithms

- **sha256** (default): Most common, 64 character hex
- **md5**: Legacy, 32 character hex
- **sha512**: Longer, more secure, 128 character hex

## Examples

### SHA-256 hash
```
file=downloaded_file.iso
algorithm=sha256
```

### MD5 hash
```
file=image.dmg
algorithm=md5
```

### Verify download
```
file=verified.zip
algorithm=sha256
expected_hash=e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
```

## Use Cases

- **Verify downloads** — match hash provided by source
- **Check integrity** — detect file corruption
- **Duplicate detection** — compare file hashes