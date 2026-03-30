---
name: draft_email_reply
description: Draft a professional email reply based on context provided by the user
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "✉️"
parameters:
  original_email:
    type: string
    description: "Original email content or summary"
  tone:
    type: string
    description: "Desired tone (formal, friendly, firm)"
    default: "professional"
  key_points:
    type: string
    description: "Key points to address in the reply"
  recipient:
    type: string
    description: "Recipient email address"
required: [original_email]
steps:
  - id: draft_reply
    type: llm
    prompt: "Draft a professional email reply with the following:\n\nOriginal email:\n{{original_email}}\n\nDesired tone: {{tone}}\nKey points to address: {{key_points}}\n\nFollow professional email conventions:\n- Appropriate greeting\n- Address each point from the original email\n- Clear call-to-action or next steps\n- Professional sign-off\n\nDo NOT include actual email addresses in the draft."
    depends_on: [original_email]
    inputs: [original_email.output]
---

# Draft Email Reply

Compose a professional, context-aware email reply.

## Usage

```bash
/draft_email_reply original_email="I wanted to follow up on the Q1 report..." tone=professional key_points="Thank them, address feedback, confirm meeting"
```

## Parameters

- **original_email**: Original email content or summary
- **tone**: Desired tone (formal, friendly, firm, default: professional)
- **key_points**: Key points to address in the reply
- **recipient**: Recipient email address

## Error Handling

- **Missing recipient:** Ask user for the email address.
- **Tone unclear:** Default to professional/friendly tone.
- **apple_mail unavailable:** Present the draft as text for manual copy-paste.
