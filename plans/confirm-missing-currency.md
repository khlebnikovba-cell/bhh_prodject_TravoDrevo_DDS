# Confirm Missing Currency

## Goal

Never assume a currency when the user did not write one. Ask the sender to choose `RUB`, `THB`, or `USD` before writing the transaction to Google Sheets.

## User Workflow

- A message with an explicit supported currency is parsed and saved immediately.
- A message without a currency is parsed but not saved. The bot replies with inline currency buttons.
- Clicking a button saves the pending transaction once and edits the prompt to show the recorded result.
- If the pending transaction no longer exists after a bot restart, the bot asks the user to send the expense again.

## Pending Data

- Keep pending transactions in application memory, keyed by a random identifier embedded in callback data.
- Store the parsed transaction, Telegram user ID, source message context, and creation time.
- Only the user who submitted the transaction may confirm it.
- Remove the pending transaction after a successful append so repeated clicks cannot create duplicates.
- Expire old pending entries before adding new ones to avoid unbounded memory growth.

## Parsing

- Return whether the currency was explicitly present instead of silently applying `DEFAULT_CURRENCY`.
- Continue parsing the amount, date, vendor, category, and description before asking for currency.
- Supported choices remain `RUB`, `THB`, and `USD`.
- Keep explicit-currency messages and pipe-separated messages backward compatible.

## Errors And Concurrency

- Do not write anything before confirmation.
- Keep simultaneous pending transactions separate, including transactions from different users.
- Reject confirmation from a different Telegram user.
- On a Sheets error, retain the pending transaction so confirmation can be retried.
- On an expired or already-used button, do not append and ask for the message again.

## Verification

- Unit tests for explicit versus missing currency detection.
- Handler tests for prompt-before-write, successful confirmation, duplicate clicks, and wrong-user clicks.
- Existing parser tests continue pass after removing the default-currency behavior.
- Run compilation and diff checks, review the implementation, fix findings, and rerun checks.
- Verify the updated bot starts successfully before creating the pull request.
