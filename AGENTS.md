# Project Workflow

These instructions apply to all implementation work in this repository.

## Before Writing Code

1. Discuss the task with the user before implementation.
2. Clarify ambiguous requirements, hidden edge cases, expected users, inputs, outputs, and failure modes.
3. Think through feature interactions explicitly, especially when the product must handle free-form text, multiple users, concurrent messages, multi-item messages, multi-message tasks, or follow-up additions from other people.
4. Write an implementation plan before changing code.
5. Save the plan in `plans/` using a descriptive kebab-case filename, for example `plans/telegram-dds-bot.md`.
6. Wait for user agreement when the plan changes product behavior, data shape, permissions, or external integrations.

## Branch And Pull Request

1. Do all work, including the plan, on a dedicated git branch.
2. Use the `codex/` branch prefix unless the user asks for another naming convention.
3. Keep commits focused and avoid mixing unrelated changes.
4. At the end of the work, push the branch and create a pull request.
5. Send the user the pull request link for review.

If no git remote is configured, say that the pull request cannot be created yet and report the current branch name.

## Review Loop

Before creating the pull request:

1. Run the relevant tests and static checks.
2. Run an initial review pass. Prefer using a subagent for code review when available.
3. Review the findings yourself.
4. Fix confirmed bugs, regressions, missing tests, unclear behavior, and documentation gaps.
5. Re-run checks after fixes.

## Planning Depth

Do not treat a broad product request as enough detail for implementation. A good plan should state:

- What user workflow is being supported.
- What cases are intentionally out of scope.
- What data model or storage shape will be used.
- How ambiguous user input will be interpreted.
- How conflicts, duplicates, edits, permissions, and errors will be handled.
- What tests or manual verification will prove the feature works.

The goal is to reduce accidental complexity before code exists, not after bugs appear.
