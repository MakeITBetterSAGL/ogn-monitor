#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TARGET="$PROJECT_DIR/database/ogn-ddb.json"
TEMP_FILE=$(mktemp "$PROJECT_DIR/database/ogn-ddb.json.XXXXXX")
trap 'rm -f "$TEMP_FILE"' EXIT

curl --fail --location --silent --show-error \
    'https://ddb.glidernet.org/download/?j=1' \
    --output "$TEMP_FILE"

python3 - "$TEMP_FILE" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    payload = json.load(source)
if not isinstance(payload.get("devices"), list):
    raise SystemExit("Invalid OGN Devices Database response")
PY

chmod 0644 "$TEMP_FILE"
mv "$TEMP_FILE" "$TARGET"
trap - EXIT
