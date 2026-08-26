#!/usr/bin/env bash
set -euo pipefail

ROOT="/var/www/padel"
cd "$ROOT"

mapfile -t changed_files < <(git status --porcelain=v1 | awk '{print $2}' | sed '/^$/d')

scope="docs"
for file in "${changed_files[@]}"; do
  case "$file" in
    docs/*|AGENTS.md|.github/workflows/*)
      ;;
    *)
      scope="app"
      break
      ;;
  esac
done

workflow_files=()
for file in "${changed_files[@]}"; do
  case "$file" in
    .github/workflows/*.yml)
      workflow_files+=("$file")
      ;;
  esac
done

if [ "${#workflow_files[@]}" -gt 0 ]; then
  python3 - "${workflow_files[@]}" <<'PY'
from pathlib import Path
import sys
import yaml

for raw in sys.argv[1:]:
    path = Path(raw)
    yaml.safe_load(path.read_text())
    print(f"yaml ok: {path}")
PY
fi

git diff --check

if [ "$scope" = "docs" ]; then
  for file in "${changed_files[@]}"; do
    case "$file" in
      docs/*|AGENTS.md)
        if [ ! -s "$file" ]; then
          echo "empty docs file: $file"
          exit 1
        fi
        python3 - "$file" <<'PY'
from pathlib import Path
import sys

Path(sys.argv[1]).read_text()
PY
        ;;
    esac
  done
  echo "scope=docs validation ok"
  exit 0
fi

bash tests/wordpress-integration.sh
echo "scope=app validation ok"
