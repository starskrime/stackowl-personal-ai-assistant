---
name: telegram_read
description: Fetch and display recent messages from a Telegram chat using the Bot API
openclaw:
  emoji: "📩"
---

# Read Telegram Messages

Fetch recent messages from a Telegram chat using the Bot API.

## Steps

1. **Get bot token** from config or memory.

2. **Fetch recent updates:**

   ```bash
   run_shell_command("curl -s 'https://api.telegram.org/bot<BOT_TOKEN>/getUpdates?limit=10' | python3 -m json.tool")
   ```

3. **Parse and display messages** showing:
   - Sender name
   - Message text
   - Timestamp

4. **Optionally filter** by chat ID or sender.

## Examples

### Get last 10 messages

```bash
run_shell_command("curl -s 'https://api.telegram.org/bot<TOKEN>/getUpdates?limit=10&offset=-10'")
```

## Error Handling

- **Unauthorized:** Token is invalid—ask user to verify.
- **No updates:** Bot may not have received messages. Ensure users have sent `/start` to the bot.
