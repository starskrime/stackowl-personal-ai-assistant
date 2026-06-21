---
name: channel-fallback
description: Deliver a message or file to the user through an alternate channel when the primary channel send fails, and record which channel succeeded.
when_to_use: When a message or file must reach the user but the primary channel send attempt fails or is unavailable. Ensures delivery is attempted on alternate reachable channels before reporting the outcome, and is honest if all channels fail.
version: 0.1.0
tags: [delivery, channel, fallback, messaging, reliability]
author: stackowl-builtin
license: MIT
---

# Channel Fallback Delivery

Reporting "message sent" after a failed send is an honesty violation. This
skill enforces attempting delivery on the primary channel first, falling back
to an alternate channel on failure, recording which channel actually accepted
the delivery, and being explicit with the user if no channel succeeded.

## Steps

1. **Attempt delivery on the primary channel.** Use `send_message` for text
   content or `send_file` for file attachments, targeting the primary channel
   or recipient. Capture the result — do not assume success from a non-error
   return alone.

2. **On failure, identify an alternate reachable channel or recipient.** Check
   which other channels or delivery targets are configured and available. Do
   not attempt channels that are known to be unreachable. Preserve the full
   message content so nothing is lost in the switch.

3. **Attempt delivery on the alternate channel.** Use `send_message` or
   `send_file` again, targeting the alternate channel. Capture the result.

4. **Record which channel succeeded.** Note the channel name or recipient that
   accepted the delivery. Include this in the response to the user so they
   know where to find the message.

5. **If all channels fail, report honestly.** Do not claim the user was
   notified. State which channels were tried, what errors occurred, and that
   delivery did not succeed.

## Verification

Before reporting the delivery outcome:

- **Confirm at least one channel actually accepted the delivery.** A successful
  result from `send_message` or `send_file` on any channel counts. If no
  channel returned a success, the user was not notified.
- **Report the channel that succeeded.** The user needs to know which channel
  received the message so they can look in the right place.
- **If all channels fail, say so explicitly.** "I was unable to reach you on
  any available channel" is the correct honest floor. Do not soften this into
  an implicit success claim.
- **Verify message content was preserved across the fallback.** Confirm the
  alternate channel send used the same message or file as the primary attempt
  — not a truncated or modified version.

## Pitfalls

- **Claiming "sent" when every channel failed.** The most critical failure
  mode: all sends fail and the agent reports success anyway. Exit status and
  error fields must be checked on every send attempt.
- **Spamming all channels simultaneously.** Attempting delivery on every
  channel at once without checking whether the primary succeeded first creates
  duplicate deliveries and user confusion. Try channels in sequence: primary
  first, then fallback.
- **Losing the message content across the fallback.** Switching to the
  alternate channel with a different, truncated, or empty message defeats the
  purpose of the fallback. Carry the original content explicitly into each
  attempt.
- **Treating an unclear or ambiguous result as success.** If the send result
  is ambiguous, treat it as a failure and attempt the next channel rather than
  assuming delivery occurred.
