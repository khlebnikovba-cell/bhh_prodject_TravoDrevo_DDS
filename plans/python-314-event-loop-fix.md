# Python 3.14 Event Loop Compatibility

## Goal

Allow the Telegram bot to start under Python 3.14, where the main thread no longer receives an event loop implicitly.

## Scope

- Create and install an asyncio event loop before `Application.run_polling()`.
- Suppress HTTP client INFO logs because Telegram API URLs contain the bot token.
- Keep Telegram handlers, parsing, Google Sheets layout, and data format unchanged.
- Verify compilation, settings loading, Google Sheets access, and live polling startup.

## Failure Handling

- Close the explicitly created event loop after polling exits.
- Preserve the existing startup exception behavior so invalid tokens and integration errors remain visible.

## Verification

- Run `py_compile` and `git diff --check`.
- Start the bot with the configured Python 3.14 environment.
- Confirm polling remains active without an event-loop exception.
- Confirm startup logs do not expose the Telegram token.
