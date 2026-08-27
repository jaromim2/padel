#!/usr/bin/env bash
set -euo pipefail

decide_retry_state() {
  local current_attempt="$1"
  local review_comment_found="$2"
  local human_review_label="$3"
  local next_attempt=$((current_attempt + 1))

  if [ "$review_comment_found" != "1" ]; then
    printf 'stop:needs-human-review:missing-review-comment\n'
    return
  fi

  if [ "$next_attempt" -gt 3 ]; then
    printf 'stop:needs-human-review:attempt-limit\n'
    return
  fi

  if [ "$human_review_label" = "1" ] && [ "$current_attempt" -ge 3 ]; then
    printf 'stop:needs-human-review:attempt-limit\n'
    return
  fi

  printf 'retry:%s\n' "$next_attempt"
}

assert_eq() {
  local expected="$1"
  local actual="$2"
  local label="$3"
  if [ "$expected" != "$actual" ]; then
    printf 'state-machine test failed for %s: expected %s, got %s\n' "$label" "$expected" "$actual" >&2
    exit 1
  fi
}

assert_eq 'retry:2' "$(decide_retry_state 1 1 1)" 'attempt-1 + changes-requested'
assert_eq 'retry:3' "$(decide_retry_state 2 1 1)" 'attempt-2 + changes-requested'
assert_eq 'stop:needs-human-review:attempt-limit' "$(decide_retry_state 3 1 1)" 'attempt-3 + changes-requested'
assert_eq 'stop:needs-human-review:missing-review-comment' "$(decide_retry_state 1 0 0)" 'missing automated review comment'

echo 'codex retry state machine ok'
