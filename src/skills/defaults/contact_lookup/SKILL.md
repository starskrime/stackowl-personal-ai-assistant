---
name: contact_lookup
description: Search macOS Contacts app for a person's email, phone number, or address
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "👤"
  os: [darwin]
parameters:
  search_term:
    type: string
    description: "Name or partial name to search for in contacts"
  detail_type:
    type: string
    description: "Type of detail to retrieve (email, phone, address, all)"
    default: "all"
required: [search_term]
steps:
  - id: search_contacts
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Contacts\" to get name of every person whose name contains \"{{search_term}}\"' 2>/dev/null || echo 'No contacts found'"
      mode: "local"
    timeout_ms: 10000
  - id: get_emails
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Contacts\" to get value of every email of (every person whose name contains \"{{search_term}}\")' 2>/dev/null || echo 'No emails found'"
      mode: "local"
    timeout_ms: 10000
    optional: true
  - id: get_phones
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Contacts\" to get value of every phone of (every person whose name contains \"{{search_term}}\")' 2>/dev/null || echo 'No phones found'"
      mode: "local"
    timeout_ms: 10000
    optional: true
  - id: get_addresses
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Contacts\" to get value of every address of (every person whose name contains \"{{search_term}}\")' 2>/dev/null || echo 'No addresses found'"
      mode: "local"
    timeout_ms: 10000
    optional: true
  - id: present_results
    type: llm
    prompt: "Present the contact lookup results for '{{search_term}}'. Format nicely with name, email(s), phone(s), and address(es) if available."
    depends_on: [search_contacts, get_emails, get_phones, get_addresses]
    inputs: [search_contacts.stdout, get_emails.stdout, get_phones.stdout, get_addresses.stdout]
---

# Contact Lookup

Search the macOS Contacts database for contact information.

## Steps

1. **Search contacts by name:**

   ```bash
   osascript -e 'tell application "Contacts" to get name of every person whose name contains "<search_term>"'
   ```

2. **Get specific details for a contact:**

   ```bash
   osascript -e 'tell application "Contacts" to get value of every email of person "<full_name>"'
   osascript -e 'tell application "Contacts" to get value of every phone of person "<full_name>"'
   ```

3. **Present the contact information** to the user.

## Examples

### Find email for John

```bash
search_term="John"
detail_type="email"
```

## Error Handling

- **No contacts found:** Suggest checking spelling or using partial name.
- **Contacts permission denied:** Guide user to System Settings > Privacy > Contacts.
- **Multiple matches:** List all and ask user to specify.
