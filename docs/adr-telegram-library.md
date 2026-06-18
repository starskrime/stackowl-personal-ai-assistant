# ADR: Telegram Client Library Selection (Story 9.1)

## Spike Status: Resolved (Story 9.1)

## Decision

**`python-telegram-bot>=21.0`** is the chosen Telegram client library for the
StackOwl v2 Telegram channel adapter.

Version 21+ is async-native (all handlers and HTTP I/O return coroutines), is
distributed under the Apache-2.0 licence, supports both long-polling and
webhook delivery, exposes inline keyboards via `InlineKeyboardMarkup`, and lets
us download voice/photo media through the standard `Bot.get_file()` →
`File.download_as_bytearray()` flow.

## Score Matrix

Each library was scored 0/1 against seven criteria.

| # | Criterion                                          | python-telegram-bot 21 | aiogram 3 | telethon 1.x |
|---|----------------------------------------------------|:----------------------:|:---------:|:------------:|
| 1 | async-native (asyncio-first, not threadpool-wrapped) |          1            |     1     |       0      |
| 2 | active commits in main within last 6 months         |          1            |     1     |       1      |
| 3 | supports both webhook and long-polling delivery     |          1            |     1     |       0      |
| 4 | handles voice messages via `getFile` + binary download |       1            |     1     |       1      |
| 5 | supports inline keyboards                           |          1            |     1     |       1      |
| 6 | compatible with Python 3.11+                        |          1            |     1     |       1      |
| 7 | OSI-approved open-source licence                    |          1            |     1     |       0      |
|   | **Total**                                           |       **7/7**         |  **7/7**  |    **4/7**   |

## Chosen Library Rationale

`python-telegram-bot` and `aiogram` both score 7/7 on the matrix. The tie was
broken in favour of `python-telegram-bot` for the following reasons:

- **Ecosystem maturity.** `python-telegram-bot` ships first-class
  `JobQueue`, `ConversationHandler`, and persistence integrations that we will
  reuse for owl scheduling and stateful interactions. aiogram covers the same
  ground but with smaller community recipes and fewer Stack Overflow answers.
- **Documentation depth.** The
  [`docs.python-telegram-bot.org`](https://docs.python-telegram-bot.org)
  site has Sphinx-rendered API references for every Bot API surface; aiogram's
  docs are functional but thinner.
- **Adapter symmetry.** Our existing `aiohttp` dependency dovetails cleanly
  with `python-telegram-bot`'s default HTTP backend (httpx-based), and the
  library exposes the bot under `Application.bot` so we can dependency-inject
  the underlying client without monkey-patching.
- **Long-term stability.** v20+ is a complete async rewrite already past the
  breaking-change wave; the API has stabilised through 21.x.

## Rejected Alternatives

### `aiogram` (3.x)

A close runner-up. Async-native, Bot API-based, MIT-licensed, supports
webhooks and inline keyboards. Rejected solely on tie-break: smaller
ecosystem, fewer third-party plugins, and less documentation coverage of
edge cases (rate-limit retries, sub-resource downloads). If the chosen
library ever blocks delivery (e.g. unfixed bug, governance change),
`aiogram` is the immediate fallback — the `ChannelAdapter` ABC keeps
implementations interchangeable.

### `telethon` (1.x)

Rejected because it speaks **MTProto** (the user-client protocol), not the
**Bot API**. Consequences:

- Criterion 3 fails: webhook delivery is not part of MTProto for bots.
- Criteria 4 and 5 are technically supported but via a *different* API
  surface than the rest of the bot ecosystem, which would force us to maintain
  bespoke wrappers.
- Criterion 7 fails: telethon ships under a non-OSI variant of the MIT
  licence with telethon-specific terms attached. Even where licence text is
  permissive, the legal review overhead is non-trivial.

Telethon remains a strong choice for *user-account* automation, which is out
of scope for StackOwl's bot-style assistant.

## ChannelAdapter Extensions Required

The Telegram surface area exceeds what the original `ChannelAdapter` ABC
covered. Three additional methods have been added as **optional** methods
(default implementations are provided, so existing CLI/Slack adapters do not
break):

- **`async send_inline_keyboard(text: str, keyboard: dict[str, object]) -> None`**
  Send a message with an inline-keyboard attachment. Default falls back to
  `send_text(text)` so plain channels degrade gracefully.

- **`async download_media(file_id: str) -> bytes`**
  Download a media attachment (voice memo, photo, document) by channel-native
  file ID. Default raises `NotImplementedError` because most channels lack
  a comparable concept.

- **`async acknowledge_callback(callback_id: str, text: str = "") -> None`**
  Acknowledge an inline-keyboard callback query. Telegram requires this within
  15 seconds of the callback; other channels treat the call as a logged no-op.

A new frozen `OutboundMessage` pydantic model was also added to
`channels/base.py` so future adapters can carry structured payloads
(`text`, `format`, `keyboard`) without expanding the method surface further.

## Implementation Notes

- Dependency added to `pyproject.toml`: `python-telegram-bot>=21.0`.
- UTF-16 code-unit counting (the formal Bot API metric) is approximated with
  Python character count in `TelegramMessageSplitter`; we keep a 3800-char
  budget against the 4096-unit limit, which is conservative for the BMP and
  slightly over-counts surrogate pairs. Pulling in `pyicu` for exact UTF-16
  counting was rejected as too heavy a dependency for the current need.
- Spike file: `tests/spikes/test_telegram_spike.py` (skipped unless
  `STACKOWL_RUN_SPIKES=1` or `--runspike`).
