# Separate DDS Journal And Summaries

## Goal

Prevent unrelated aggregate and transaction rows from appearing as one combined row in Google Sheets.

## Sheet Structure

- Keep `Transactions` as the append-only source ledger.
- Make `DDS` one coherent cashflow journal with columns: date, type, project, category, vendor, amount, currency, and description.
- Create `Summary` for independent aggregate tables.
- Put monthly totals and project/category totals in separated blocks on `Summary`, with blank columns between them.

## Migration

- Rebuild the generated `DDS` and `Summary` sheets on startup from formulas backed by `Transactions`.
- Preserve all source rows in `Transactions` and all vendor aliases in `Library`.
- Do not modify transaction amounts, currencies, dates, or descriptions during this migration.

## Ordering And Currency

- Sort the DDS journal by transaction date descending.
- Keep amount and currency from the same source row in one query result.
- Group every summary by currency and never combine totals across currencies.

## Errors And Scope

- Empty ledgers should show headers without formula errors.
- Multiple currencies and multiple transactions on the same date remain distinct rows.
- Formatting and exchange-rate conversion are out of scope.

## Verification

- Unit-test the generated formulas and their destination ranges.
- Run existing parser and currency-confirmation tests.
- Apply the migration to the configured Google Sheet and inspect returned values.
- Run compilation and diff checks, review findings, fix confirmed issues, and rerun checks.
- Restart the live bot before opening the pull request.
