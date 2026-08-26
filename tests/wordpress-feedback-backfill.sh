#!/usr/bin/env bash
set -euo pipefail

ROOT="/var/www/padel"
SITE_DIR="$ROOT/site"
COOKIE_JAR="/tmp/padel-feedback-cookie.txt"
DRY_RUN_JSON="/tmp/padel-feedback-dry-run.json"
RUN_JSON="/tmp/padel-feedback-run.json"
FORCE_JSON="/tmp/padel-feedback-force.json"
REVIEW_JSON="/tmp/padel-feedback-review.json"
SUMMARY_JSON="/tmp/padel-feedback-summary.json"
ANALYSIS_ID="185"

ADMIN_USER="$(awk -F= '/^ADMIN_USER=/{print $2}' "$SITE_DIR/.padel-credentials.txt")"
ADMIN_PASS="$(awk -F= '/^ADMIN_PASS=/{print $2}' "$SITE_DIR/.padel-credentials.txt")"

curl -sS -c "$COOKIE_JAR" -b "$COOKIE_JAR" http://padel.local/wp-login.php >/dev/null
curl -sS -c "$COOKIE_JAR" -b "$COOKIE_JAR" \
  -d "log=$ADMIN_USER&pwd=$ADMIN_PASS&wp-submit=Log+In&redirect_to=http%3A%2F%2Fpadel.local%2Fwp-admin%2F&testcookie=1" \
  http://padel.local/wp-login.php >/dev/null

NONCE="$(curl -sS -b "$COOKIE_JAR" http://padel.local/wp-admin/ | sed -n 's/.*"nonce":"\([^"]*\)".*/\1/p' | head -n 1)"
if [ -z "$NONCE" ]; then
  echo "failed to extract REST nonce"
  exit 1
fi

baseline_checksum="$(wp eval --path="$SITE_DIR" --allow-root '
$result = Padel_Video_Analysis::instance()->get_meta('"$ANALYSIS_ID"', "analysis_result", []);
if (!is_array($result)) { $result = []; }
unset($result["technical_feedback"]);
echo hash("sha256", json_encode($result, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES));
')"
baseline_manual_contact="$(wp eval --path="$SITE_DIR" --allow-root 'echo (string) Padel_Video_Analysis::instance()->get_meta('"$ANALYSIS_ID"', "manual_contact_frame_index", 0);')"
baseline_review_versions="$(wp eval --path="$SITE_DIR" --allow-root 'echo count(Padel_Video_Analysis_Review_Store::instance()->get_review_versions('"$ANALYSIS_ID"'));')"

dry_run_output="$(wp padel-video-analysis backfill-feedback --user="$ADMIN_USER" --analysis-ids="$ANALYSIS_ID" --dry-run --path="$SITE_DIR" --allow-root)"
printf '%s\n' "$dry_run_output" > "$DRY_RUN_JSON"

dry_run_status="$(php -r '$j=json_decode(file_get_contents("'"$DRY_RUN_JSON"'"), true); echo (($j["summary"]["dry_run"] ?? false) === true) ? "yes" : "no";')"
if [ "$dry_run_status" != "yes" ]; then
  echo "dry run flag was not preserved"
  exit 1
fi

dry_run_analysis_status="$(php -r '$j=json_decode(file_get_contents("'"$DRY_RUN_JSON"'"), true); echo $j["results"][0]["status"] ?? "";')"
if [ "$dry_run_analysis_status" != "updated" ]; then
  echo "dry run did not report an eligible update"
  exit 1
fi

post_dry_run_checksum="$(wp eval --path="$SITE_DIR" --allow-root '
$result = Padel_Video_Analysis::instance()->get_meta('"$ANALYSIS_ID"', "analysis_result", []);
if (!is_array($result)) { $result = []; }
unset($result["technical_feedback"]);
echo hash("sha256", json_encode($result, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES));
')"
post_dry_run_tf_version="$(wp eval --path="$SITE_DIR" --allow-root 'echo (string) Padel_Video_Analysis::instance()->get_meta('"$ANALYSIS_ID"', "technical_feedback_rules_version", "");')"
if [ "$baseline_checksum" != "$post_dry_run_checksum" ] || [ -n "$post_dry_run_tf_version" ]; then
  echo "dry run modified stored analysis data"
  exit 1
fi

run_output="$(wp padel-video-analysis backfill-feedback --user="$ADMIN_USER" --analysis-ids="$ANALYSIS_ID" --path="$SITE_DIR" --allow-root)"
printf '%s\n' "$run_output" > "$RUN_JSON"

run_status="$(php -r '$j=json_decode(file_get_contents("'"$RUN_JSON"'"), true); echo $j["results"][0]["status"] ?? "";')"
if [ "$run_status" != "updated" ]; then
  echo "backfill did not update the analysis"
  cat "$RUN_JSON"
  exit 1
fi

post_run_tf_version="$(wp eval --path="$SITE_DIR" --allow-root '
$result = Padel_Video_Analysis::instance()->get_meta('"$ANALYSIS_ID"', "analysis_result", []);
echo (string) ($result["technical_feedback"]["rules_version"] ?? "");
')"
post_run_generation_mode="$(wp eval --path="$SITE_DIR" --allow-root '
$result = Padel_Video_Analysis::instance()->get_meta('"$ANALYSIS_ID"', "analysis_result", []);
echo (string) ($result["technical_feedback"]["generation_mode"] ?? "");
')"
post_run_source_analysis_id="$(wp eval --path="$SITE_DIR" --allow-root '
$result = Padel_Video_Analysis::instance()->get_meta('"$ANALYSIS_ID"', "analysis_result", []);
echo (string) ($result["technical_feedback"]["source_analysis_id"] ?? "");
')"
post_run_calculated_at="$(wp eval --path="$SITE_DIR" --allow-root '
$result = Padel_Video_Analysis::instance()->get_meta('"$ANALYSIS_ID"', "analysis_result", []);
echo (string) ($result["technical_feedback"]["calculated_at"] ?? "");
')"
post_run_checksum="$(wp eval --path="$SITE_DIR" --allow-root '
$result = Padel_Video_Analysis::instance()->get_meta('"$ANALYSIS_ID"', "analysis_result", []);
if (!is_array($result)) { $result = []; }
unset($result["technical_feedback"]);
echo hash("sha256", json_encode($result, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES));
')"
post_run_manual_contact="$(wp eval --path="$SITE_DIR" --allow-root 'echo (string) Padel_Video_Analysis::instance()->get_meta('"$ANALYSIS_ID"', "manual_contact_frame_index", 0);')"

if [ "$post_run_tf_version" != "technical-feedback-v1" ] || [ "$post_run_generation_mode" != "backfill" ] || [ "$post_run_source_analysis_id" != "$ANALYSIS_ID" ] || [ -z "$post_run_calculated_at" ]; then
  echo "backfilled technical feedback metadata is incomplete"
  exit 1
fi

if [ "$baseline_checksum" != "$post_run_checksum" ] || [ "$baseline_manual_contact" != "$post_run_manual_contact" ]; then
  echo "backfill changed pose/stroke data or manual contact frame"
  exit 1
fi

repeat_output="$(wp padel-video-analysis backfill-feedback --user="$ADMIN_USER" --analysis-ids="$ANALYSIS_ID" --path="$SITE_DIR" --allow-root)"
printf '%s\n' "$repeat_output" > /tmp/padel-feedback-repeat.json
repeat_status="$(php -r '$j=json_decode(file_get_contents("/tmp/padel-feedback-repeat.json"), true); echo $j["results"][0]["reason"] ?? "";')"
if [ "$repeat_status" != "already_current" ]; then
  echo "repeat backfill was not idempotent"
  exit 1
fi

sleep 1
force_output="$(wp padel-video-analysis backfill-feedback --user="$ADMIN_USER" --analysis-ids="$ANALYSIS_ID" --force --path="$SITE_DIR" --allow-root)"
printf '%s\n' "$force_output" > "$FORCE_JSON"
force_status="$(php -r '$j=json_decode(file_get_contents("'"$FORCE_JSON"'"), true); echo $j["results"][0]["status"] ?? "";')"
if [ "$force_status" != "updated" ]; then
  echo "force backfill did not overwrite the current version"
  exit 1
fi

force_calculated_at="$(wp eval --path="$SITE_DIR" --allow-root '
$result = Padel_Video_Analysis::instance()->get_meta('"$ANALYSIS_ID"', "analysis_result", []);
echo (string) ($result["technical_feedback"]["calculated_at"] ?? "");
')"
if [ "$force_calculated_at" = "$post_run_calculated_at" ]; then
  echo "force backfill did not refresh the calculation timestamp"
  exit 1
fi

analysis_feedback_json="$(wp eval --path="$SITE_DIR" --allow-root '
$result = Padel_Video_Analysis::instance()->get_meta('"$ANALYSIS_ID"', "analysis_result", []);
echo json_encode($result["technical_feedback"] ?? [], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
')"
printf '%s\n' "$analysis_feedback_json" > /tmp/padel-feedback-analysis.json

review_payload="$(php -r '
$feedback=json_decode(file_get_contents("/tmp/padel-feedback-analysis.json"), true);
if (!is_array($feedback) || !isset($feedback["rules_version"])) { fwrite(STDERR, "feedback missing\n"); exit(1); }
$items = [];
foreach (["findings", "possible_observations", "neutral_measurements", "withheld_findings"] as $bucket) {
  foreach (($feedback[$bucket] ?? []) as $entry) {
    if (!is_array($entry)) continue;
    $items[] = [
      "item_kind" => $bucket === "findings" ? "finding" : ($bucket === "possible_observations" ? "possible_observation" : ($bucket === "neutral_measurements" ? "neutral_measurement" : "withheld_finding")),
      "rule_id" => (string) ($entry["rule_id"] ?? ""),
      "title" => (string) ($entry["title"] ?? ($entry["rule_id"] ?? "")),
      "review_label" => "correct",
      "severity" => "verified",
      "reviewer_note" => "seeded by backfill workflow test",
      "timestamp_correct" => true,
      "wording_correct" => true,
      "should_have_been_withheld" => false,
      "measurement_reasonable" => true,
      "supporting_timestamp_ms" => $entry["primary_timestamp_ms"] ?? null,
      "supporting_frame_index" => $entry["primary_frame_index"] ?? null,
    ];
    break 2;
  }
}
if ($items === []) {
  fwrite(STDERR, "backfilled analysis did not expose a reviewable item\n");
  exit(1);
}
echo json_encode([
  "rules_version" => $feedback["rules_version"],
  "overall_video_suitability" => "usable",
  "overall_analysis_usefulness" => "partly useful",
  "items" => $items,
], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
')"

review_response="$(curl -sS -b "$COOKIE_JAR" -H "X-WP-Nonce: $NONCE" -H 'Content-Type: application/json' \
  -d "$review_payload" \
  "http://padel.local/wp-json/padel-video-analysis/v1/analysis/$ANALYSIS_ID/review")"
printf '%s\n' "$review_response" > "$REVIEW_JSON"

review_ok="$(php -r '$j=json_decode(file_get_contents("'"$REVIEW_JSON"'"), true); echo (($j["success"] ?? false) === true) ? "yes" : "no";')"
if [ "$review_ok" != "yes" ]; then
  echo "saving calibration review failed"
  cat "$REVIEW_JSON"
  exit 1
fi

summary_response="$(curl -sS -b "$COOKIE_JAR" -H "X-WP-Nonce: $NONCE" "http://padel.local/wp-json/padel-video-analysis/v1/reviews/calibration-summary")"
printf '%s\n' "$summary_response" > "$SUMMARY_JSON"
summary_rows="$(php -r '$j=json_decode(file_get_contents("'"$SUMMARY_JSON"'"), true); echo count($j["summary"]["rule_rows"] ?? []);')"
if [ "$summary_rows" -lt 1 ]; then
  echo "calibration summary did not capture the seeded review"
  exit 1
fi

review_versions_after="$(wp eval --path="$SITE_DIR" --allow-root 'echo count(Padel_Video_Analysis_Review_Store::instance()->get_review_versions('"$ANALYSIS_ID"'));')"
if [ "$review_versions_after" -lt 1 ] || [ "$baseline_review_versions" -ne 0 ]; then
  echo "review record validation failed"
  exit 1
fi

echo "wordpress feedback backfill ok"
