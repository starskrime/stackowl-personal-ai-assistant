---
name: draft_email_reply
description: Draft a professional email reply based on context provided by the user
openclaw:
  emoji: "✉️"
---

# Draft Email Reply

Compose a professional, context-aware email reply.

## Steps

1. **Collect context from the user:**
   - Original email content or summary
   - Desired tone (formal, friendly, firm)
   - Key points to address

2. **Draft the reply** following professional email conventions:
   - Appropriate greeting
   - Address each point from the original email
   - Clear call-to-action or next steps
   - Professional sign-off

3. **Present the draft** to the user for review.

4. **If approved, send via apple_mail:**
   ```yaml
   apple_mail:
     action: "send"
     to: "<recipient>"
     subject: "Re: <original subject>"
     body: "<drafted reply>"
   ```

## Examples

### Formal reply

```yaml
apple_mail:
  action: "send"
  to: "boss@company.com"
  subject: "Re: Q1 Report Review"
  body: "Dear John,\n\nThank you for your feedback on the Q1 report..."
```

## Error Handling

- **Missing recipient:** Ask user for the email address.
- **Tone unclear:** Default to professional/friendly tone.
- **apple_mail unavailable:** Present the draft as text for manual copy-paste.
