---
name: encrypt_file
description: Encrypt or decrypt files using AES-256 encryption via OpenSSL with a password
openclaw:
  emoji: "🛡️"
---

# File Encryption

Encrypt and decrypt files with AES-256.

## Steps

1. **Encrypt a file:**
   ```bash
   run_shell_command("openssl enc -aes-256-cbc -salt -pbkdf2 -in <file> -out <file.enc>")
   ```
   (Will prompt for password)
2. **Decrypt:**
   ```bash
   run_shell_command("openssl enc -aes-256-cbc -d -pbkdf2 -in <file.enc> -out <file>")
   ```
3. **Confirm** the operation completed.

## Examples

### Encrypt a document

```bash
run_shell_command("openssl enc -aes-256-cbc -salt -pbkdf2 -in secret.txt -out secret.txt.enc")
```

## Error Handling

- **Wrong password on decrypt:** OpenSSL will report "bad decrypt" — ask user to retry.
- **Original file cleanup:** Ask if user wants to delete the unencrypted original after encryption.
