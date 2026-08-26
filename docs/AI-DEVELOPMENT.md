# AI Development Guide

This repository uses Codex for controlled implementation and review.

## Implementation loop
- Trigger implementation work when a GitHub Issue receives the `codex-ready` label.
- Read `AGENTS.md`, `docs/PRODUCT.md`, and the issue content before editing.
- Use the branch name `codex/issue-<number>`.
- Keep edits inside the checked-out repository workspace.
- Run the relevant tests before a pull request is created or updated.
- Commit and push only after the local test pass for that iteration.
- Limit automated implementation or fix iterations to three per issue.

## Review loop
- Run Codex review separately from implementation.
- Review jobs should have read-only repository permissions.
- Post the review result directly on the pull request.
- If the review requests changes, the fix workflow may run again.
- Do not auto-merge after a successful review.

## State handling
- Preserve the original issue, pull request, and review history.
- Use a hidden marker in the pull request body to track the associated issue and iteration count.
- Reject additional automated iterations after the third attempt.

## Security rules
- Never expose `OPENAI_API_KEY`.
- Use `GITHUB_TOKEN` only for branch push, PR creation, PR updates, and review posting.
- Do not write secrets into committed files, logs, or comments.

## Manual handoff
When the automation succeeds, the final state should be ready for user testing.
Humans remain responsible for final product validation and merge decisions.

