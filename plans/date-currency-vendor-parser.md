# Date, Currency, And Vendor Parsing

## Goal

Parse natural-language expense messages without confusing dates or subscription durations with monetary amounts, and normalize known website/service names through a Google Sheets library.

## User Workflow

The user sends one transaction per Telegram message. For example:

`3 сентября покупка тарифа ультра на хигсвел - 1 месяц за 275 долларов`

The bot records 3 September 2026, an expense of 275 USD, category `Подписки`, vendor `Higgsfield`, and a cleaned description.

## Data Model

Append these columns to `Transactions` so existing rows remain compatible:

- `currency`: ISO currency code such as `RUB`, `USD`, or `THB`.
- `vendor`: canonical service/vendor name.
- `source_text`: original Telegram message for audit and future reparsing.

Create `Library` with:

- `canonical_name`
- `aliases`
- `default_category`

Seed `Higgsfield` with aliases `хигсвел`, `хигсфилд`, and `higgsfield`, category `Подписки`.

## Parsing Rules

- Support `сегодня`, `вчера`, `DD.MM`, `DD.MM.YYYY`, and Russian `D <month> [year]` dates.
- Use the current year when omitted; if that would produce a future date, use the previous year.
- Prefer amounts explicitly adjacent to a currency marker or following payment words such as `за`, `оплатил`, or `стоимость`.
- Exclude date fragments and duration fragments such as `1 месяц` from amount candidates.
- Use `RUB` when no currency is specified.
- Recognize `USD`, `RUB`, and `THB` plus common Russian words and symbols.
- Match aliases case-insensitively and use the library's canonical name and category.
- For unknown vendors, preserve a candidate only in an explicit subscription/service context; do not invent a correction.
- Continue supporting the existing compact and pipe-separated formats.

## DDS And Summary

- Group monthly and project/category totals by currency.
- Never add balances from different currencies together.
- Return `/summary` as one income/expense/balance block per currency.

## Conflicts And Errors

- Continue accepting only one transaction per message.
- Reject messages with no unambiguous amount.
- Keep `source_text` to make corrections auditable.
- Telegram message ID remains the traceable source identifier.

## Out Of Scope

- Automatic exchange-rate conversion.
- Multiple transactions in one message.
- A transaction assembled across multiple messages or users.
- AI-based vendor guessing.

## Verification

- Unit tests for dates, currencies, duration conflicts, aliases, pipe format, and invalid input.
- Compilation and diff checks.
- Live bot test against Telegram and the configured Google Sheet.
- Correct the existing malformed test row after the migration is verified.
- Run a review pass, fix confirmed findings, and rerun all checks before opening the PR.
