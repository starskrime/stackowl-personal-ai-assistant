---
name: slack_message
description: Send a message to a Slack channel or user via the Slack API using curl
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "💬"
parameters:
  channel:
    type: string
    description: "Slack channel ID or name (e.g., C01234ABC or #general)"
  message:
    type: string
    description: "The message text to send"
  bot_token:
    type: string
    description: "Slack Bot Token (from config if not provided)"
    default: ""
required: [channel, message]
steps:
  - id: send_message
    tool: ShellTool
    args:
      command: "curl -s -X POST 'https://slack.com/api/chat.postMessage' -H 'Authorization: Bearer {{bot_token}}' -H 'Content-Type: application/json' -d '{\"channel\": \"{{channel}}\", \"text\": \"{{message}}\"}'"
      mode: "local"
    timeout_ms: 15000
  - id: verify_send
    type: llm
    prompt: "Parse the Slack API response and confirm if the message was sent successfully. Look for \"ok\": true in the response.\n\nResponse: {{send_message.output}}"
    depends_on: [send_message]
    inputs: [send_message.output]
---

# Send Slack Message

Send messages to Slack channels or users using the Bot API.

## Usage

```bash
/slack_message channel=#general message="Deploy complete!"
/slack_message channel=C01234ABC message="Build failed on main"
```

## Parameters

- **channel**: Slack channel ID or name (e.g., C01234ABC or #general) (required)
- **message**: The message text to send (required)
- **bot_token**: Slack Bot Token (from config if not provided)

## Error Handling

- **`"not_authed"`:** Bot token is invalid. Ask user to check token.
- **`"channel_not_found"`:** Ask user for correct channel name/ID.
- **No token available:** Check `stackowl.config.json` for `slack.botToken`.
