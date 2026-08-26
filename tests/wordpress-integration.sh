#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${GITHUB_WORKSPACE:-$(cd "$SCRIPT_DIR/.." && pwd)}"
SITE_DIR="$ROOT/site"
COOKIE_JAR="/tmp/padel-wordpress-cookie.txt"
UPLOAD_JSON="/tmp/padel-wordpress-upload.json"
STATUS_JSON="/tmp/padel-wordpress-status.json"
JOB_JSON="/tmp/padel-wordpress-job.json"
SERVICE_URL="${PADEL_ANALYSIS_SERVICE_URL:-http://127.0.0.1:8010}"
SERVICE_SECRET="${PADEL_ANALYSIS_SERVICE_SECRET:-padel-local-dev-secret}"

source "$ROOT/tests/service-utils.sh"
ensure_analysis_service

ADMIN_USER="$(awk -F= '/^ADMIN_USER=/{print $2}' "$SITE_DIR/.padel-credentials.txt")"
ADMIN_PASS="$(awk -F= '/^ADMIN_PASS=/{print $2}' "$SITE_DIR/.padel-credentials.txt")"
ADMIN_USER_ID="$(wp user get "$ADMIN_USER" --field=ID --path="$SITE_DIR" --allow-root)"

EXISTING_IDS="$(wp post list --path="$SITE_DIR" --post_type=padel_video_analysis --author="$ADMIN_USER_ID" --fields=ID --format=ids --allow-root)"
if [ -n "$EXISTING_IDS" ]; then
  wp post delete $EXISTING_IDS --force --path="$SITE_DIR" --allow-root
fi

curl -sS -c "$COOKIE_JAR" -b "$COOKIE_JAR" http://padel.local/wp-login.php >/dev/null
curl -sS -c "$COOKIE_JAR" -b "$COOKIE_JAR" \
  -d "log=$ADMIN_USER&pwd=$ADMIN_PASS&wp-submit=Log+In&redirect_to=http%3A%2F%2Fpadel.local%2Fwp-admin%2F&testcookie=1" \
  http://padel.local/wp-login.php >/dev/null

NONCE="$(curl -sS -b "$COOKIE_JAR" http://padel.local/wp-admin/ | sed -n 's/.*"nonce":"\([^"]*\)".*/\1/p' | head -n 1)"
if [ -z "$NONCE" ]; then
  echo "failed to extract REST nonce"
  exit 1
fi

ffmpeg -f lavfi -i color=c=black:s=320x240:d=31 -pix_fmt yuv420p -y /tmp/padel-wordpress-video.mp4 >/tmp/padel-wordpress-ffmpeg.log 2>&1

upload_response="$(curl -sS -b "$COOKIE_JAR" -H "X-WP-Nonce: $NONCE" \
  -F shot_type=forehand \
  -F dominant_hand=right \
  -F camera_angle=rear \
  -F video_file=@/tmp/padel-wordpress-video.mp4 \
  http://padel.local/wp-json/padel-video-analysis/v1/upload)"
printf '%s\n' "$upload_response" > "$UPLOAD_JSON"

analysis_id="$(php -r '$j=json_decode(file_get_contents("'"$UPLOAD_JSON"'"), true); if (!is_array($j) || !($j["success"] ?? false)) { fwrite(STDERR, "upload failed\n"); exit(1);} echo $j["analysis"]["id"] ?? "";')"
if [ -z "$analysis_id" ]; then
  echo "upload response missing analysis id"
  exit 1
fi

duplicate_response="$(curl -sS -b "$COOKIE_JAR" -H "X-WP-Nonce: $NONCE" \
  -F shot_type=forehand \
  -F dominant_hand=right \
  -F camera_angle=rear \
  -F video_file=@/tmp/padel-wordpress-video.mp4 \
  http://padel.local/wp-json/padel-video-analysis/v1/upload)"
duplicate_id="$(php -r '$j=json_decode(file_get_contents("php://stdin"), true); echo $j["analysis"]["id"] ?? "";' <<<"$duplicate_response")"
if [ "$duplicate_id" = "$analysis_id" ]; then
  echo "duplicate upload reused the same analysis"
  exit 1
fi
echo "duplicate upload created a new analysis"

for _ in 1 2 3 4 5 6 7 8; do
  status_response="$(curl -sS -b "$COOKIE_JAR" -H "X-WP-Nonce: $NONCE" "http://padel.local/wp-json/padel-video-analysis/v1/analysis/$analysis_id")"
  printf '%s\n' "$status_response" > "$STATUS_JSON"
  status="$(php -r '$j=json_decode(file_get_contents("'"$STATUS_JSON"'"), true); echo $j["analysis"]["status"] ?? "";')"
  if [ "$status" = "completed" ]; then
    break
  fi
  sleep 2
done

final_status="$(php -r '$j=json_decode(file_get_contents("'"$STATUS_JSON"'"), true); echo $j["analysis"]["status"] ?? "";')"
if [ "$final_status" != "completed" ]; then
  echo "analysis did not complete"
  cat "$STATUS_JSON"
  exit 1
fi

external_job_id="$(php -r '$j=json_decode(file_get_contents("'"$STATUS_JSON"'"), true); echo $j["analysis"]["external"]["job_id"] ?? "";')"
if [ -z "$external_job_id" ]; then
  echo "external job id missing"
  exit 1
fi

result_summary="$(php -r '$j=json_decode(file_get_contents("'"$STATUS_JSON"'"), true); echo $j["analysis"]["result"]["summary"] ?? "";')"
if [[ "$result_summary" != *"Video analysis completed successfully"* ]]; then
  echo "unexpected result summary: $result_summary"
  exit 1
fi

result_processor="$(php -r '$j=json_decode(file_get_contents("'"$STATUS_JSON"'"), true); echo $j["analysis"]["result"]["processor"] ?? "";')"
if [ "$result_processor" != "opencv_heuristic_video_analyzer" ]; then
  echo "unexpected result processor: $result_processor"
  exit 1
fi

if [ -n "$duplicate_id" ]; then
  curl -sS -X DELETE -b "$COOKIE_JAR" -H "X-WP-Nonce: $NONCE" "http://padel.local/wp-json/padel-video-analysis/v1/analysis/$duplicate_id" >/dev/null
fi

curl -sS -H "X-Padel-API-Secret: $SERVICE_SECRET" "$SERVICE_URL/api/v1/jobs/$external_job_id" > "$JOB_JSON"
download_url="$(php -r '$j=json_decode(file_get_contents("'"$JOB_JSON"'"), true); echo $j["external_artifacts"]["download_url"] ?? "";')"
if [ -z "$download_url" ]; then
  echo "download url missing from service job payload"
  exit 1
fi

case "$download_url" in
  *"/private-download/"*"?token="*) ;;
  *)
    echo "download url is not tokenized: $download_url"
    exit 1
    ;;
esac

fresh_download_url="$(wp eval --path="$SITE_DIR" --allow-root 'echo Padel_Video_Analysis::instance()->build_private_video_download_url('"$analysis_id"');')"
if [ -z "$fresh_download_url" ]; then
  echo "failed to mint a fresh private download url"
  exit 1
fi

download_status="$(curl -sS -o /dev/null -w '%{http_code}' "$fresh_download_url")"
if [ "$download_status" != "200" ]; then
  echo "fresh tokenized private download failed with status $download_status"
  exit 1
fi

reuse_status="$(curl -sS -o /tmp/padel-private-download.json -w '%{http_code}' "$fresh_download_url")"
if [ "$reuse_status" != "401" ] && [ "$reuse_status" != "403" ]; then
  echo "reused private download token unexpectedly returned $reuse_status"
  cat /tmp/padel-private-download.json
  exit 1
fi

delete_response="$(curl -sS -X DELETE -b "$COOKIE_JAR" -H "X-WP-Nonce: $NONCE" "http://padel.local/wp-json/padel-video-analysis/v1/analysis/$analysis_id")"
delete_success="$(php -r '$j=json_decode(file_get_contents("php://stdin"), true); echo $j["success"] ? "yes" : "no";' <<<"$delete_response")"
if [ "$delete_success" != "yes" ]; then
  echo "delete failed"
  exit 1
fi

remaining="$(wp post list --path="$SITE_DIR" --post_type=padel_video_analysis --fields=ID --format=count --allow-root)"
if [ "$remaining" != "0" ]; then
  echo "expected no remaining analyses, found $remaining"
  exit 1
fi

service_http_code="$(curl -sS -o /tmp/padel-service-delete-check.json -w '%{http_code}' -H "X-Padel-API-Secret: $SERVICE_SECRET" "$SERVICE_URL/api/v1/jobs/$external_job_id")"
if [ "$service_http_code" != "404" ]; then
  echo "service job still retrievable after delete"
  cat /tmp/padel-service-delete-check.json
  exit 1
fi

echo "wordpress integration ok"
