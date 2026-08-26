# AGENTS.md

## Repository purpose
This repository contains the `padel` WordPress plugin and its supporting analysis service.
Use this repository to improve the existing product, but do not change product behavior unless the issue explicitly asks for it.

## Codex operating rules
- Read `AGENTS.md`, `docs/PRODUCT.md`, and the linked issue or pull request context before making changes.
- Modify only the checked-out workspace for the task at hand.
- Keep changes scoped to the requested issue.
- Do not introduce unrelated refactors, cleanup, or dependency upgrades.
- Never expose secrets, tokens, or private URLs in code, logs, comments, pull requests, issue comments, or generated files.
- Do not print `OPENAI_API_KEY` or copy it into temporary files that are committed or uploaded.
- Prefer small, reviewable commits.

## Workflow expectations
- Branch naming for Codex automation must follow `codex/issue-<number>`.
- Run the relevant tests before opening or updating a pull request.
- Do not auto-merge pull requests.
- Leave the final successful state for human testing and review.
- Respect a maximum of three automated implementation or fix iterations per issue.

## Testing
- Run the smallest meaningful test set first, then expand if the change touches shared code.
- If a test fails, fix only the failure that is directly related to the requested work.
- Preserve existing user data, stored analysis results, and review history.

## Review guidance
- Favor evidence-backed changes over speculative edits.
- When reviewing, focus on correctness, regressions, security, and test coverage.
- Keep review comments specific and actionable.

