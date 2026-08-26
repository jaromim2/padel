#!/usr/bin/env bash
set -euo pipefail

ROOT="/var/www/padel"
SITE_DIR="$ROOT/site"
COOKIE_JAR="/tmp/padel-review-cookie.txt"
UPLOAD_JSON="/tmp/padel-review-upload.json"
REVIEW_JSON="/tmp/padel-review-response.json"
REVIEW_GET_JSON="/tmp/padel-review-get.json"
SUMMARY_JSON="/tmp/padel-review-summary.json"
SERVICE_URL="${PADEL_ANALYSIS_SERVICE_URL:-http://127.0.0.1:8010}"
SERVICE_SECRET="${PADEL_ANALYSIS_SERVICE_SECRET:-padel-local-dev-secret}"

source "$ROOT/tests/service-utils.sh"
ensure_analysis_service

ADMIN_USER="$(awk -F= '/^ADMIN_USER=/{print $2}' "$SITE_DIR/.padel-credentials.txt")"
ADMIN_PASS="$(awk -F= '/^ADMIN_PASS=/{print $2}' "$SITE_DIR/.padel-credentials.txt")"
ADMIN_USER_ID="$(wp user get "$ADMIN_USER" --field=ID --path="$SITE_DIR" --allow-root)"

curl -sS -c "$COOKIE_JAR" -b "$COOKIE_JAR" http://padel.local/wp-login.php >/dev/null
curl -sS -c "$COOKIE_JAR" -b "$COOKIE_JAR" \
  -d "log=$ADMIN_USER&pwd=$ADMIN_PASS&wp-submit=Log+In&redirect_to=http%3A%2F%2Fpadel.local%2Fwp-admin%2F&testcookie=1" \
  http://padel.local/wp-login.php >/dev/null

NONCE="$(curl -sS -b "$COOKIE_JAR" http://padel.local/wp-admin/ | sed -n 's/.*"nonce":"\([^"]*\)".*/\1/p' | head -n 1)"
if [ -z "$NONCE" ]; then
  echo "failed to extract REST nonce"
  exit 1
fi

ffmpeg -f lavfi -i color=c=black:s=1280x720:d=12 -pix_fmt yuv420p -y /tmp/padel-review-video.mp4 >/tmp/padel-review-ffmpeg.log 2>&1

upload_response="$(curl -sS -b "$COOKIE_JAR" -H "X-WP-Nonce: $NONCE" \
  -F shot_type=forehand \
  -F dominant_hand=right \
  -F camera_angle=side \
  -F video_file=@/tmp/padel-review-video.mp4 \
  http://padel.local/wp-json/padel-video-analysis/v1/upload)"
printf '%s\n' "$upload_response" > "$UPLOAD_JSON"

analysis_id="$(php -r '$j=json_decode(file_get_contents("'"$UPLOAD_JSON"'"), true); if (!is_array($j) || !($j["success"] ?? false)) { fwrite(STDERR, "upload failed\n"); exit(1);} echo $j["analysis"]["id"] ?? "";')"
if [ -z "$analysis_id" ]; then
  echo "upload response missing analysis id"
  exit 1
fi

for _ in 1 2 3 4 5 6 7 8 9 10; do
  status_response="$(curl -sS -b "$COOKIE_JAR" -H "X-WP-Nonce: $NONCE" "http://padel.local/wp-json/padel-video-analysis/v1/analysis/$analysis_id")"
  printf '%s\n' "$status_response" > "$REVIEW_GET_JSON"
  status="$(php -r '$j=json_decode(file_get_contents("'"$REVIEW_GET_JSON"'"), true); echo $j["analysis"]["status"] ?? "";')"
  if [ "$status" = "completed" ]; then
    break
  fi
  sleep 2
done

analysis_before_checksum="$(php -r '$j=json_decode(file_get_contents("'"$REVIEW_GET_JSON"'"), true); echo hash("sha256", json_encode($j["analysis"]["result"] ?? [], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES));')"

manual_rule_id="$(wp eval --path="$SITE_DIR" --allow-root 'echo Padel_Video_Analysis::instance()->get_review_rule_catalog()["rules"][0]["id"] ?? "";')"
if [ -z "$manual_rule_id" ]; then
  echo "failed to load review rule catalog"
  exit 1
fi

review_payload="$(php -r '
$j=json_decode(file_get_contents("'"$REVIEW_GET_JSON"'"), true);
if (!is_array($j) || !isset($j["analysis"])) { fwrite(STDERR, "analysis payload missing\n"); exit(1); }
$analysis = $j["analysis"];
$feedback = $analysis["result"]["technical_feedback"] ?? [];
$items = [];
foreach (["findings" => ["correct", "moderate"], "possible_observations" => ["partially_correct", "minor"], "neutral_measurements" => ["visually_reasonable", "minor"], "withheld_findings" => ["correct", "minor"]] as $bucket => $defaults) {
  $entries = $feedback[$bucket] ?? [];
  foreach ($entries as $entry) {
    if (!is_array($entry)) continue;
    $items[] = [
      "item_kind" => $bucket === "findings" ? "finding" : ($bucket === "possible_observations" ? "possible_observation" : ($bucket === "neutral_measurements" ? "neutral_measurement" : "withheld_finding")),
      "rule_id" => (string)($entry["rule_id"] ?? ""),
      "title" => (string)($entry["title"] ?? ($entry["rule_id"] ?? "")),
      "review_label" => $defaults[0],
      "severity" => $defaults[1],
      "reviewer_note" => "checked in workflow test",
      "timestamp_correct" => true,
      "wording_correct" => true,
      "should_have_been_withheld" => false,
      "measurement_reasonable" => true,
      "supporting_timestamp_ms" => $entry["primary_timestamp_ms"] ?? null,
      "supporting_frame_index" => $entry["primary_frame_index"] ?? null,
    ];
  }
}
$payload = [
  "rules_version" => $feedback["rules_version"] ?? "",
  "overall_video_suitability" => "usable",
  "overall_analysis_usefulness" => "partly useful",
  "items" => $items,
  "manual_missed_findings" => [[
    "manual_rule_id" => "'"$manual_rule_id"'",
    "confidence_level" => "medium",
    "manual_miss_kind" => "missed_completely",
    "supporting_timestamp_ms" => 1000,
    "supporting_frame_index" => 25,
    "reviewer_note" => "manual missed finding added by workflow test",
  ]],
];
echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
')"
base_review_payload="$review_payload"

review_response="$(curl -sS -b "$COOKIE_JAR" -H "X-WP-Nonce: $NONCE" -H 'Content-Type: application/json' \
  -d "$review_payload" \
  "http://padel.local/wp-json/padel-video-analysis/v1/analysis/$analysis_id/review")"
printf '%s\n' "$review_response" > "$REVIEW_JSON"

review_success="$(php -r '$j=json_decode(file_get_contents("'"$REVIEW_JSON"'"), true); echo ($j["success"] ?? false) ? "yes" : "no";')"
if [ "$review_success" != "yes" ]; then
  echo "saving review failed"
  cat "$REVIEW_JSON"
  exit 1
fi

review_item_count="$(php -r '$j=json_decode(file_get_contents("'"$REVIEW_JSON"'"), true); echo count($j["review"]["items"] ?? []);')"
if [ "$review_item_count" -lt 1 ]; then
  echo "saved review missing items"
  exit 1
fi

analysis_after_checksum="$(php -r '$j=json_decode(file_get_contents("'"$REVIEW_JSON"'"), true); echo hash("sha256", json_encode($j["analysis"]["result"] ?? [], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES));')"
if [ "$analysis_before_checksum" != "$analysis_after_checksum" ]; then
  echo "automatic result changed after review"
  exit 1
fi

invalid_review_payload="$(php -r '
$payload=json_decode(file_get_contents("'"$REVIEW_GET_JSON"'"), true)["analysis"]["review"] ?? [];
if (!is_array($payload)) { fwrite(STDERR, "saved review payload missing\n"); exit(1); }
$payload["rules_version"] = "workflow-test-invalid";
$payload["manual_missed_findings"][0]["manual_rule_id"] = "not_a_real_rule";
echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
')"
invalid_review_response="$(curl -sS -b "$COOKIE_JAR" -H "X-WP-Nonce: $NONCE" -H 'Content-Type: application/json' \
  -d "$invalid_review_payload" \
  "http://padel.local/wp-json/padel-video-analysis/v1/analysis/$analysis_id/review")"
invalid_review_code="$(php -r '$j=json_decode(file_get_contents("php://stdin"), true); echo $j["code"] ?? "";' <<<"$invalid_review_response")"
if [ "$invalid_review_code" != "padel_invalid_review_rule" ]; then
  echo "unsupported rule id was not rejected"
  exit 1
fi

second_rules_version="workflow-test-v2"
second_review_payload="$(php -r '
$payload=json_decode(file_get_contents("php://stdin"), true);
if (!is_array($payload)) { fwrite(STDERR, "base review payload missing\n"); exit(1); }
$payload["rules_version"] = "'"$second_rules_version"'";
echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
' <<<"$base_review_payload")"
second_review_response="$(curl -sS -b "$COOKIE_JAR" -H "X-WP-Nonce: $NONCE" -H 'Content-Type: application/json' \
  -d "$second_review_payload" \
  "http://padel.local/wp-json/padel-video-analysis/v1/analysis/$analysis_id/review")"
printf '%s\n' "$second_review_response" > "$REVIEW_JSON"

second_review_ok="$(php -r '$j=json_decode(file_get_contents("'"$REVIEW_JSON"'"), true); echo (($j["review"]["rules_version"] ?? "") === "'"$second_rules_version"'") ? "yes" : "no";')"
if [ "$second_review_ok" != "yes" ]; then
  echo "second review version was not saved"
  exit 1
fi

review_get="$(curl -sS -b "$COOKIE_JAR" -H "X-WP-Nonce: $NONCE" "http://padel.local/wp-json/padel-video-analysis/v1/analysis/$analysis_id/review")"
printf '%s\n' "$review_get" > "$REVIEW_GET_JSON"
review_count="$(php -r '$j=json_decode(file_get_contents("'"$REVIEW_GET_JSON"'"), true); echo count($j["analysis"]["review"]["items"] ?? []);')"
if [ "$review_count" -eq 0 ]; then
  echo "review did not persist"
  exit 1
fi

review_version_count="$(wp eval --path="$SITE_DIR" --allow-root 'echo count(Padel_Video_Analysis_Review_Store::instance()->get_review_versions('"$analysis_id"'));')"
if [ "$review_version_count" -lt 2 ]; then
  echo "review version history missing"
  exit 1
fi

summary_response="$(curl -sS -b "$COOKIE_JAR" -H "X-WP-Nonce: $NONCE" "http://padel.local/wp-json/padel-video-analysis/v1/reviews/calibration-summary")"
printf '%s\n' "$summary_response" > "$SUMMARY_JSON"
summary_ok="$(php -r '$j=json_decode(file_get_contents("'"$SUMMARY_JSON"'"), true); echo isset($j["summary"]["rule_rows"]) ? "yes" : "no";')"
if [ "$summary_ok" != "yes" ]; then
  echo "calibration summary missing"
  exit 1
fi

subscriber_login="padel-review-subscriber"
subscriber_pass="PadelReview123!"
subscriber_id="$(wp user get "$subscriber_login" --field=ID --path="$SITE_DIR" --allow-root 2>/dev/null || true)"
if [ -z "$subscriber_id" ]; then
  subscriber_id="$(wp user create "$subscriber_login" "$subscriber_login@example.com" --role=subscriber --user_pass="$subscriber_pass" --path="$SITE_DIR" --allow-root --porcelain)"
fi

subscriber_no_review="$(wp eval --path="$SITE_DIR" --allow-root '
$user = get_user_by("id", '"$subscriber_id"');
wp_set_current_user((int) $user->ID);
$post = get_post('"$analysis_id"');
$payload = Padel_Video_Analysis::instance()->get_record_payload($post);
echo isset($payload["review"]) ? "yes" : "no";
')"
if [ "$subscriber_no_review" != "no" ]; then
  echo "normal user unexpectedly saw review data"
  exit 1
fi

subscriber_review_forbidden="$(wp eval --path="$SITE_DIR" --allow-root '
$user = get_user_by("id", '"$subscriber_id"');
wp_set_current_user((int) $user->ID);
$request = new WP_REST_Request("GET", "/padel-video-analysis/v1/reviews/calibration-summary");
$response = rest_do_request($request);
echo (string) $response->get_status();
')"
if [ "$subscriber_review_forbidden" = "200" ]; then
  echo "normal user unexpectedly accessed calibration summary"
  exit 1
fi

curl -sS -X DELETE -b "$COOKIE_JAR" -H "X-WP-Nonce: $NONCE" "http://padel.local/wp-json/padel-video-analysis/v1/analysis/$analysis_id" >/dev/null
wp user delete "$subscriber_id" --reassign="$ADMIN_USER_ID" --path="$SITE_DIR" --allow-root --yes >/dev/null

echo "wordpress review workflow ok"
