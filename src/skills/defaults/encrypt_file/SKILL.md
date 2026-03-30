---
name: encrypt_file
description: Encrypt or decrypt files using AES-256 encryption via OpenSSL with a password
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🛡️"
parameters:
  file:
    type: string
    description: "Path to the file to encrypt or decrypt"
  action:
    type: string
    description: "Action to perform: encrypt or decrypt"
    default: "encrypt"
  password:
    type: string
    description: "Password for encryption/decryption"
required: [file, action]
steps:
  - id: check_file
    tool: ReadFileTool
    args:
      path: "{{file}}"
  - id: encrypt
    tool: ShellTool
    args:
      command: "openssl enc -aes-256-cbc -salt -pbkdf2 -in {{file}} -out {{file}}.enc"
      mode: "local"
    timeout_ms: 30000
    optional: true
  - id: decrypt
    tool: ShellTool
    args:
      command: "openssl enc -aes-256-cbc -d -pbkdf2 -in {{file}} -out {{file}}.decrypted"
      mode: "local"
    timeout_ms: 30000
    optional: true
---

# File Encryption

Encrypt and decrypt files with AES-256.

## Usage

```bash
/encrypt_file file=<path> action=<encrypt|decrypt> password=<password>
```

## Parameters

- **file**: Path to the file to encrypt or decrypt
- **action**: Action to perform: encrypt or decrypt (default: encrypt)
- **password**: Password for encryption/decryption

## Examples

### Encrypt a document

```
file=secret.txt
action=encrypt
password=mysecretpass
```

### Decrypt a document

```
file=secret.txt.enc
action=decrypt
password=mysecretpass
```

## Error Handling

- **Wrong password on decrypt:** OpenSSL will report "bad decrypt" — ask user to retry.
- **Original file cleanup:** Ask if user wants to delete the unencrypted original after encryption.
