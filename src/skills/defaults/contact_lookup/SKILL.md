---
name: contact_lookup
description: Search macOS Contacts app for a person's email, phone number, or address
openclaw:
  emoji: "👤"
  os: [darwin]
---

# Contact Lookup

Search the macOS Contacts database for contact information.

## Steps

1. **Search contacts by name:**
   ```bash
   run_shell_command("osascript -e 'tell application \"Contacts\" to get name of every person whose name contains \"<search_term>\"'")
   ```

2. **Get specific details for a contact:**
   ```bash
   run_shell_command("osascript -e 'tell application \"Contacts\" to get value of every email of person \"<full_name>\"'")
   run_shell_command("osascript -e 'tell application \"Contacts\" to get value of every phone of person \"<full_name>\"'")
   ```

3. **Present the contact information** to the user.

## Examples

### Find email for John
```bash
run_shell_command("osascript -e 'tell application \"Contacts\" to get {name, value of email 1} of every person whose name contains \"John\"'")
```

## Error Handling

- **No contacts found:** Suggest checking spelling or using partial name.
- **Contacts permission denied:** Guide user to System Settings > Privacy > Contacts.
- **Multiple matches:** List all and ask user to specify.
