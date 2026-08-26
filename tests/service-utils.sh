#!/usr/bin/env bash
set -euo pipefail

ROOT="/var/www/padel"

start_analysis_service() {
  docker compose -f "$ROOT/padel-analysis-service/docker-compose.yml" up -d --build padel-analysis-service >/tmp/padel-analysis-service-start.log 2>&1
}

wait_for_analysis_service() {
  local service_url="${PADEL_ANALYSIS_SERVICE_URL:-http://127.0.0.1:8010}"
  local service_secret="${PADEL_ANALYSIS_SERVICE_SECRET:-padel-local-dev-secret}"
  local attempt
  for attempt in $(seq 1 30); do
    if curl -fsS "$service_url/health/live" >/dev/null && \
       curl -fsS -H "X-Padel-API-Secret: $service_secret" "$service_url/health/ready" >/dev/null; then
      return 0
    fi
    sleep 2
  done
  echo "service health check failed"
  return 1
}

ensure_analysis_service() {
  start_analysis_service
  wait_for_analysis_service
}

