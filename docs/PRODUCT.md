# Product Overview

`padel` is a WordPress plugin and analysis service for padel video workflows.

## Product goals
- Support the existing padel analysis application.
- Keep the WordPress plugin as the user-facing entry point.
- Keep the analysis service isolated from the plugin UI.
- Preserve private handling of uploaded videos and generated artifacts.
- Use deterministic and testable automation wherever possible.

## Current automation goal
This repository is configured for Codex-assisted development and review.

Codex tasks in this repository should:
- start from a GitHub Issue or Pull Request context,
- make changes only in the checked-out workspace,
- run the relevant tests before creating or updating a PR,
- push changes to a dedicated `codex/issue-<number>` branch,
- leave the result ready for human testing rather than auto-merging.

## Desired lifecycle
1. A GitHub Issue is labeled `codex-ready`.
2. Codex creates or updates a task branch.
3. Codex edits the workspace and runs the relevant tests.
4. Codex opens or updates a pull request.
5. Codex review runs separately on the PR.
6. If changes are requested, Codex fixes the branch and re-runs tests.
7. When the review passes, the PR is left in a `user-testing` state.

## Non-goals for automation
- Do not leak secrets.
- Do not auto-merge.
- Do not change the padel product architecture unless the issue explicitly requests it.
- Do not bypass the existing review flow.

