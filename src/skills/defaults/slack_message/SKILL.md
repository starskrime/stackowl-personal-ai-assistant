---
name: slack_message
description: Send a message to a Slack channel or user via the Slack API using curl
openclaw:
  emoji: "💬"
---

# Send Slack Message

Send messages to Slack channels or users using the Bot API.

## Steps

1. **Resolve prerequisites:**
   - Slack Bot Token (from config or user)
   - Channel ID or user ID

2. **Send the message via curl:**

   ```bash
   run_shell_command("curl -s -X POST 'https://slack.com/api/chat.postMessage' -H 'Authorization: Bearer <BOT_TOKEN>' -H 'Content-Type: application/json' -d '{\"channel\": \"<CHANNEL_ID>\", \"text\": \"<message>\"}'")
   ```

3. **Parse response** and confirm `"ok": true`.

## Examples

### Send to a channel

```bash
run_shell_command("curl -s -X POST 'https://slack.com/api/chat.postMessage' -H 'Authorization: Bearer xoxb-token' -H 'Content-Type: application/json' -d '{\"channel\": \"C01234ABC\", \"text\": \"Deploy complete ✅\"}'")
```

## Error Handling

- **`"not_authed"`:** Bot token is invalid. Ask user to check token.
- **`"channel_not_found"`:** Ask user for correct channel name/ID.
- **No token available:** Check `stackowl.config.json` for `slack.botToken`.
