---
name: telegram_read
description: Fetch and display recent messages from a Telegram chat using the Bot API
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "📩"
parameters:
  bot_token:
    type: string
    description: "Telegram bot token (or from config)"
  limit:
    type: number
    description: "Number of messages to fetch"
    default: "10"
  chat_id:
    type: string
    description: "Optional chat ID to filter messages"
required: []
steps:
  - id: fetch_messages
    tool: ShellTool
    args:
      command: "curl -s 'https://api.telegram.org/bot{{bot_token}}/getUpdates?limit={{limit}}' | python3 -m json.tool"
      mode: "local"
    timeout_ms: 15000
  - id: parse_messages
    type: llm
    prompt: "Parse and present these Telegram messages in a clean format showing sender name, message text, and timestamp for each message:\n\n{{fetch_messages.output}}"
    depends_on: [fetch_messages]
    inputs: [fetch_messages.output]
---

# Read Telegram Messages

Fetch recent messages from a Telegram chat using the Bot API.

## Usage

```bash
/telegram_read bot_token=YOUR_TOKEN limit=10
```

## Parameters

- **bot_token**: Telegram bot token (or from config)
- **limit**: Number of messages to fetch (default: 10)
- **chat_id**: Optional chat ID to filter messages

## Error Handling

- **Unauthorized:** Token is invalid—ask user to verify.
- **No updates:** Bot may not have received messages. Ensure users have sent `/start` to the bot.
