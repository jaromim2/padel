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
- Post the review as a normal PR conversation comment, not a formal GitHub review.
- Include the marker `<!-- codex-automated-review -->` in the automated review comment.
- Use labels to represent the review outcome:
  - `user-testing` for approved review results
  - `changes-requested` when Codex requests changes
  - `needs-human-review` when the review is unclear or blocked
- If the review requests changes, the `changes-requested` label starts the next fix attempt.
- Do not auto-merge after a successful review.

## State handling
- Preserve the original issue, pull request, and review history.
- Store the Codex review output in an artifact between the review and posting jobs.
- Reject additional automated iterations after the third attempt.

## Security rules
- Never expose `OPENAI_API_KEY`.
- Use `GITHUB_TOKEN` only for branch push, PR creation, PR updates, label changes, and review comments.
- Do not write secrets into committed files, logs, or comments.

## Manual handoff
When the automation succeeds, the final state should be ready for user testing.
Humans remain responsible for final product validation and merge decisions.
